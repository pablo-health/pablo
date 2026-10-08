# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A fallback model for long structured calls: drafting, importing, deriving.

Each call site is built the way the app builds it, with no gateway passed
in, so these exercise the real wiring: ``AI_FALLBACKS`` in the
environment, the provider registry resolving each model to its gateway,
and the hedged gateway running the legs. The models are ``test:`` ids
served by one stub that fails the primary as each test tells it to.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.reliability import RetryExhaustedError
from app.services import structured_llm_gateway
from app.services.ai_features import AIFeature
from app.services.hedged_structured_llm_gateway import (
    GENERATION_BUDGET_SECONDS,
    HedgedStructuredLLMGateway,
    ProviderStructuredLLMGateway,
    generation_gateway,
)
from app.services.http_structured_llm_gateway import HttpStructuredLLMGateway
from app.services.note_generation_service import (
    RegistryNoteGenerationService,
    TransientNoteGenerationError,
)
from app.services.note_import_service import NoteImportService
from app.services.note_type_derive_service import NoteTypeDeriveService
from app.services.structured_llm_gateway import (
    StructuredCompletion,
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    register_structured_llm_provider,
)
from app.services.therapy_labels import TIME_KEY
from app.settings import get_settings

from .test_note_type_derive import PROPOSAL

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

PRIMARY = "test:primary"
FALLBACK = "test:fallback"

NOW = datetime(2026, 10, 6, 15, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)
TRANSCRIPT = Transcript(
    format="txt",
    content=(
        "[00:00:05] Therapist: How has the week been?\n"
        "[00:00:09] Client: Better. I slept most nights.\n"
        "[00:02:10] Therapist: Let's get into the session work.\n"
        "[00:02:15] Client: Sure."
    ),
)

#: Text that must never reach a log line: it stands for what a clinician
#: said and what a model wrote back.
SAID = "Jane Roe said the word lighthouse"
WRITTEN = "Fallback wrote about the lighthouse"


def _transient(status: int = 503) -> RuntimeError:
    """What a provider's gateway raises when its single attempt is rate limited or down."""
    response = httpx.Response(status, request=httpx.Request("POST", "https://model.invalid"))
    error = httpx.HTTPStatusError(f"{status}", request=response.request, response=response)
    exhausted = RetryExhaustedError(attempts=1, last_exc=error)
    exhausted.__cause__ = error
    wrapped = RuntimeError(f"Structured LLM call failed: {status}")
    wrapped.__cause__ = exhausted
    return wrapped


def _permanent() -> RuntimeError:
    """A model that will not serve the request at all (no access to it)."""
    return RuntimeError("Structured LLM call failed: AccessDeniedException, HTTP 403")


def instance(schema: dict[str, Any], text: str = WRITTEN) -> Any:
    """A value of the schema's shape, the way a model fills one."""
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = next(k for k in kind if k != "null")
    if kind == "object":
        return {name: instance(sub, text) for name, sub in schema.get("properties", {}).items()}
    if kind == "array":
        return [instance(schema.get("items") or {"type": "string"}, text)]
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    if "enum" in schema:
        return next(v for v in schema["enum"] if v is not None)
    return text


class Provider(StructuredLLMGateway):
    """Serves ``test:`` models. The primary fails with each queued error, then answers."""

    def __init__(
        self,
        *,
        primary: list[Exception] | None = None,
        fallback: list[Exception] | None = None,
        answer: Callable[[dict[str, Any]], dict[str, Any]] = instance,
    ) -> None:
        self._errors = {PRIMARY: list(primary or []), FALLBACK: list(fallback or [])}
        self._answer = answer
        self.calls: list[tuple[str, int]] = []
        self._lock = threading.Lock()

    def complete_structured(
        self, *, model: str, response_schema: dict[str, Any], max_output_tokens: int, **_: Any
    ) -> StructuredCompletion:
        with self._lock:
            self.calls.append((model, max_output_tokens))
            errors = self._errors[model]
            error = errors.pop(0) if errors else None
        if error is not None:
            raise error
        return StructuredCompletion(data=self._answer(response_schema))

    @property
    def models(self) -> list[str]:
        return [model for model, _ in self.calls]


@pytest.fixture
def fallbacks(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Name one fallback for each long-call feature, as a deployment would."""
    keys = ("note_generation", "note_import", "note_type_derive")
    monkeypatch.setenv("AI_FALLBACKS", json.dumps(dict.fromkeys(keys, FALLBACK)))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def serve() -> Iterator[Callable[[Provider], Provider]]:
    """Register a provider for ``test:`` model ids for the length of a test."""

    def register(provider: Provider) -> Provider:
        register_structured_llm_provider("test", lambda: provider)
        return provider

    yield register
    structured_llm_gateway._registered_providers.pop("test", None)


def _registry() -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    return registry


def _generator() -> RegistryNoteGenerationService:
    return RegistryNoteGenerationService(registry=_registry(), model=PRIMARY)


_DIAGNOSES_SPEC = PracticeNoteTypeSpec.model_validate(
    {
        "label": "Evaluation",
        "sections": [
            {
                "key": "assessment",
                "label": "Assessment",
                "fields": [
                    {"key": "diagnoses", "label": "Diagnoses", "kind": "diagnoses"},
                    {"key": "formulation", "label": "Formulation"},
                ],
            }
        ],
    }
)

_PSYCHOTHERAPY_SPEC = PracticeNoteTypeSpec.model_validate(
    {
        "label": "Follow-up",
        "sections": [
            {
                "key": "medication",
                "label": "Medication management",
                "fields": [{"key": "summary", "label": "Summary"}],
            },
            {
                "key": "psychotherapy",
                "label": "Psychotherapy",
                "fields": [{"key": "interventions", "label": "Interventions"}],
            },
        ],
    }
)


class TestWiring:
    def test_with_nothing_configured_each_call_site_keeps_its_one_model(self) -> None:

        assert isinstance(
            RegistryNoteGenerationService()._llm_gateway, ProviderStructuredLLMGateway
        )
        assert isinstance(NoteImportService()._llm_gateway, ProviderStructuredLLMGateway)
        derive = NoteTypeDeriveService(NoteImportService())
        assert isinstance(derive._llm_gateway, ProviderStructuredLLMGateway)
        stand_in = HttpStructuredLLMGateway("http://stand-in.invalid")
        assert generation_gateway(AIFeature.NOTE_GENERATION, stand_in) is stand_in

    def test_unnamed_a_call_goes_once_to_its_own_models_provider(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        """A prefixed model is served by its provider, not the default gateway."""
        provider = serve(Provider(primary=[_transient()]))
        gateway = generation_gateway("an_extension_feature")

        with pytest.raises(RuntimeError, match="503"):
            gateway.complete_structured(
                model=PRIMARY,
                system_prompt="s",
                user_prompt="u",
                response_schema={"type": "object"},
                max_output_tokens=64,
            )
        assert provider.models == [PRIMARY]

    def test_a_feature_can_swap_its_primary_and_fall_back_to_the_default(
        self, monkeypatch: pytest.MonkeyPatch, serve: Callable[[Provider], Provider]
    ) -> None:
        """``ai_models`` names the primary; the old default can be its fallback."""
        monkeypatch.setenv("AI_MODELS", json.dumps({"note_generation": PRIMARY}))
        monkeypatch.setenv("AI_FALLBACKS", json.dumps({"note_generation": FALLBACK}))
        get_settings.cache_clear()
        provider = serve(Provider(primary=[_transient()]))
        definition = to_definition("custom.eval", 1, _DIAGNOSES_SPEC)
        try:
            service = RegistryNoteGenerationService(registry=_registry())
            generated = service.generate_note(
                definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition
            )
            assert get_settings().model_for(AIFeature.NOTE_IMPORT, "flash") == "flash"
        finally:
            get_settings.cache_clear()

        assert generated.content["assessment"]["formulation"] == WRITTEN
        assert provider.models == [PRIMARY, FALLBACK]

    def test_a_feature_not_named_has_no_fallback_whatever_else_is_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No default list: the interactive list and other features' entries stay theirs."""
        monkeypatch.setenv("AI_MODEL_FLASH_FALLBACKS", "test:interactive")
        monkeypatch.setenv("AI_FALLBACKS", json.dumps({"note_import": FALLBACK}))
        get_settings.cache_clear()
        try:
            settings = get_settings()
            assert settings.fallbacks_for(AIFeature.NOTE_GENERATION) == ()
            assert settings.fallbacks_for(AIFeature.CHAT) == ()
            assert settings.fallbacks_for(AIFeature.NOTE_IMPORT) == (FALLBACK,)
            assert settings.fallbacks_for(AIFeature.AVAILABILITY_PARSE) == ("test:interactive",)

            gateway = RegistryNoteGenerationService()._llm_gateway
            assert isinstance(gateway, ProviderStructuredLLMGateway)
            assert isinstance(NoteImportService()._llm_gateway, HedgedStructuredLLMGateway)
        finally:
            get_settings.cache_clear()

    def test_availability_parse_named_in_the_map_wins_over_its_old_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AI_MODEL_FLASH_FALLBACKS", "test:interactive")
        monkeypatch.setenv(
            "AI_FALLBACKS", json.dumps({"availability_parse": f"{FALLBACK}, test:second"})
        )
        get_settings.cache_clear()
        try:
            hedged = HedgedStructuredLLMGateway.from_settings()
            legs = [leg.model for leg in hedged.policy_for("flash", 15.0).legs]
        finally:
            get_settings.cache_clear()
        assert legs == ["flash", FALLBACK, "test:second", "flash"]

    @pytest.mark.usefixtures("fallbacks")
    def test_configured_each_call_site_runs_the_fallback_order_one_at_a_time(self) -> None:
        for gateway in (
            RegistryNoteGenerationService()._llm_gateway,
            NoteImportService()._llm_gateway,
            NoteTypeDeriveService(NoteImportService())._llm_gateway,
        ):
            assert isinstance(gateway, HedgedStructuredLLMGateway)
            policy = gateway.policy_for("gemini-pro", None)
            assert [leg.model for leg in policy.legs] == ["gemini-pro", FALLBACK, "gemini-pro"]
            assert policy.hedge_after is None, "a long call is never raced"
            assert policy.budget == GENERATION_BUDGET_SECONDS
            assert {leg.timeout for leg in policy.legs} == {180.0}

    def test_the_fallback_starts_only_once_the_primary_has_run_out_of_time(self) -> None:
        """No stall threshold: the primary keeps its whole attempt to itself."""
        release = threading.Event()
        started: dict[str, float] = {}

        class Stalls(StructuredLLMGateway):
            def complete_structured(self, *, model: str, **_: Any) -> StructuredCompletion:
                started[model] = time.monotonic()
                if model == PRIMARY:
                    release.wait(5)
                return StructuredCompletion(data={"ok": True})

        pool = ThreadPoolExecutor(max_workers=4)
        gateway = HedgedStructuredLLMGateway(
            fallbacks=[FALLBACK],
            stall_after=None,
            resolve=lambda _model: Stalls(),
            executor=pool,
            budget=GENERATION_BUDGET_SECONDS,
        )
        began = time.monotonic()
        try:
            completion = gateway.complete_structured(
                model=PRIMARY,
                system_prompt="s",
                user_prompt="u",
                response_schema={"type": "object"},
                max_output_tokens=64,
                timeout_seconds=0.5,
            )
        finally:
            release.set()
            pool.shutdown(wait=False, cancel_futures=True)
        assert completion.data == {"ok": True}
        assert started[FALLBACK] - began >= 0.5


@pytest.mark.usefixtures("fallbacks")
class TestNoteDrafting:
    def test_a_soap_draft_is_answered_by_the_fallback(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(Provider(primary=[_transient(), _transient()]))

        generated = _generator().generate_note("soap", TRANSCRIPT, PATIENT, NOW)

        assert generated.soap_note is not None
        assert generated.soap_note.subjective.chief_complaint.text == WRITTEN
        assert provider.models[:2] == [PRIMARY, FALLBACK]

    def test_a_diagnoses_field_parses_from_the_fallback(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(Provider(primary=[_transient(429)]))
        definition = to_definition("custom.eval", 1, _DIAGNOSES_SPEC)

        generated = _generator().generate_note(
            definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition
        )

        (diagnosis,) = generated.content["assessment"]["diagnoses"]
        assert diagnosis["label"] == WRITTEN
        assert generated.content["assessment"]["formulation"] == WRITTEN
        assert provider.models == [PRIMARY, FALLBACK]

    def test_the_dictated_psychotherapy_time_parses_from_the_fallback(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        def answer(schema: dict[str, Any]) -> dict[str, Any]:
            data: dict[str, Any] = instance(schema)
            if TIME_KEY in data:
                data[TIME_KEY] = {
                    "start": "10:14",
                    "end": "10:55",
                    "minutes": 41,
                    "as_dictated": "Psychotherapy from 10:14 to 10:55, 41 minutes.",
                }
            return data

        serve(Provider(primary=[_transient(), _transient()], answer=answer))
        definition = to_definition("custom.follow_up", 1, _PSYCHOTHERAPY_SPEC)

        generated = _generator().generate_note(
            definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition
        )

        assert generated.content["psychotherapy"]["interventions"] == WRITTEN
        assert generated.psychotherapy_proposal is not None
        assert generated.psychotherapy_proposal["dictated"]["minutes"] == 41
        assert generated.content["psychotherapy"]["psychotherapy_time"] == (
            "10:14 to 10:55, 41 minutes"
        )

    def test_a_fallback_that_refuses_does_not_mask_the_primarys_answer(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(Provider(primary=[_transient()], fallback=[_permanent()]))
        definition = to_definition("custom.eval", 1, _DIAGNOSES_SPEC)

        generated = _generator().generate_note(
            definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition
        )

        assert generated.content["assessment"]["formulation"] == WRITTEN
        assert provider.models == [PRIMARY, FALLBACK, PRIMARY]

    def test_a_primary_that_answers_is_never_doubled(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(Provider(fallback=[_permanent()]))
        definition = to_definition("custom.eval", 1, _DIAGNOSES_SPEC)

        _generator().generate_note(definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition)

        assert provider.models == [PRIMARY]

    def test_truncation_goes_straight_to_the_larger_budget_not_the_fallback(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(Provider(primary=[StructuredOutputTruncatedError("cut off")]))
        definition = to_definition("custom.eval", 1, _DIAGNOSES_SPEC)

        _generator().generate_note(definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition)

        base = get_settings().note_max_output_tokens
        assert provider.calls == [(PRIMARY, base), (PRIMARY, base * 2)]

    def test_rate_limited_everywhere_is_left_to_the_job_queues_retry(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(
            Provider(primary=[_transient(429), _transient(429)], fallback=[_transient(429)])
        )
        definition = to_definition("custom.eval", 1, _DIAGNOSES_SPEC)

        with pytest.raises(TransientNoteGenerationError):
            _generator().generate_note(
                definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition
            )

        assert provider.models == [PRIMARY, FALLBACK, PRIMARY], "bounded: no retry inside a leg"

    def test_no_prompt_or_answer_text_is_logged(
        self, serve: Callable[[Provider], Provider], caplog: pytest.LogCaptureFixture
    ) -> None:
        serve(Provider(primary=[_transient()], fallback=[_permanent()]))
        definition = to_definition("custom.eval", 1, _DIAGNOSES_SPEC)
        transcript = Transcript(format="txt", content=f"[00:00:05] Client: {SAID}")

        with caplog.at_level(logging.DEBUG):
            _generator().generate_note(
                definition.key, transcript, PATIENT, NOW, definition=definition
            )

        logged = "\n".join(
            [r.getMessage() for r in caplog.records] + [r.exc_text or "" for r in caplog.records]
        )
        assert "route=retry" in logged
        assert "lighthouse" not in logged


@pytest.mark.usefixtures("fallbacks")
class TestNoteImport:
    def test_an_imported_note_is_parsed_from_the_fallback(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(Provider(primary=[_transient(), _transient()]))
        service = NoteImportService(model=PRIMARY)

        parsed = service.parse_soap_note("Subjective: slept better.\nPlan: return in two weeks.")

        assert parsed.content["subjective"]["chief_complaint"]["text"] == WRITTEN
        assert provider.models == [PRIMARY, FALLBACK]


@pytest.mark.usefixtures("fallbacks")
class TestNoteTypeDerive:
    def test_a_proposal_is_answered_by_the_fallback(
        self, serve: Callable[[Provider], Provider]
    ) -> None:
        provider = serve(
            Provider(primary=[_transient(), _transient()], answer=lambda _schema: PROPOSAL)
        )
        service = NoteTypeDeriveService(NoteImportService(model=PRIMARY), model=PRIMARY)

        derived = service.derive([], description="An interval history, then a plan.")

        assert [s.key for s in derived.spec.sections] == ["interval", "plan"]
        assert provider.models == [PRIMARY, FALLBACK]
