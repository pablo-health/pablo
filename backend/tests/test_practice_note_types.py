# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note types a practice defines for itself.

Covers the stored definition's validation, how the registry resolves a
practice type beside the built-ins (versions, retirement), what generation
sends the model (the practice's prompt with the floor after it, the inputs,
the template), and the save / retire / read routes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from app.auth.service import get_current_user, require_baa_acceptance
from app.main import app
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import (
    GENERATION_FLOOR,
    PracticeNoteTypeSpec,
    RepositoryPracticeNoteTypeSource,
    render_user_prompt,
    to_definition,
    validate_note_inputs,
)
from app.repositories import (
    InMemoryPracticeNoteTypeRepository,
    get_practice_note_type_repository,
)
from app.routes.note_types import get_registry
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError

NOW = datetime(2026, 9, 23, tzinfo=UTC)

COACH_SPEC: dict[str, Any] = {
    "label": "Interview Coach",
    "description": "Coaching summary for a discovery call.",
    "system_prompt": "You are an interview coach for customer discovery calls.",
    "user_template": (
        "Segment: {inputs.segment}\nOffer: {inputs.offer}\nDate: {session_date}\n\n"
        '{fields}\n\nReturn JSON like {"a": 1}.\n\nTranscript:\n{transcript}'
    ),
    "sections": [
        {
            "key": "fix",
            "label": "One thing to fix",
            "fields": [{"key": "one_thing", "label": "One thing", "ai_hint": "One habit."}],
        },
        {
            "key": "log_row",
            "label": "Log row",
            "fields": [{"key": "channels", "label": "Channels", "kind": "list"}],
        },
    ],
    "inputs": [
        {
            "key": "segment",
            "label": "Segment",
            "kind": "choice",
            "options": ["Network", "Starter", "Prescriber"],
            "required": True,
        },
        {"key": "offer", "label": "Micro offer"},
    ],
}


def _spec(**overrides: Any) -> PracticeNoteTypeSpec:
    return PracticeNoteTypeSpec.model_validate({**COACH_SPEC, **overrides})


def _registry(repo: InMemoryPracticeNoteTypeRepository) -> NoteTypeRegistry:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    registry.set_practice_source(RepositoryPracticeNoteTypeSource(lambda: repo))
    return registry


def _save(repo: InMemoryPracticeNoteTypeRepository, **overrides: Any) -> None:
    repo.add_version(
        "custom.coach",
        _spec(**overrides).model_dump(mode="json"),
        created_by="u-1",
        created_at=NOW,
    )


class TestSpecValidation:
    def test_accepts_the_coach(self) -> None:
        assert _spec().label == "Interview Coach"

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"user_template": "Segment: {inputs.segment}"}, "{transcript}"),
            ({"user_template": "{inputs.nope} {transcript}"}, "undeclared"),
            (
                {"inputs": [{"key": "a", "label": "A"}, {"key": "a", "label": "A2"}]},
                "duplicate input keys",
            ),
            (
                {"inputs": [{"key": "a", "label": "A", "kind": "choice", "options": ["x"]}]},
                "at least two options",
            ),
            ({"inputs": [{"key": "a", "label": "A", "options": ["x", "y"]}]}, "takes no options"),
            ({"sections": []}, "at least 1"),
        ],
    )
    def test_rejects(self, overrides: dict[str, Any], message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            _spec(**overrides)


class TestDefinition:
    def test_floor_follows_the_practice_prompt(self) -> None:
        definition = to_definition("custom.coach", 1, _spec())

        assert definition.system_prompt is not None
        assert definition.system_prompt.startswith("You are an interview coach")
        assert definition.system_prompt.endswith(GENERATION_FLOOR)

    def test_empty_prompt_still_gets_the_floor(self) -> None:
        definition = to_definition("custom.coach", 1, _spec(system_prompt="  "))

        assert definition.system_prompt is not None
        assert definition.system_prompt.endswith(GENERATION_FLOOR)

    def test_carries_version_and_inputs(self) -> None:
        definition = to_definition("custom.coach", 3, _spec())

        assert definition.version == 3
        assert [i.key for i in definition.inputs] == ["segment", "offer"]
        assert definition.context == "session"


class TestInputs:
    def _definition(self) -> Any:
        return to_definition("custom.coach", 1, _spec())

    def test_keeps_valid_and_drops_blank(self) -> None:
        kept = validate_note_inputs(self._definition(), {"segment": " Starter ", "offer": " "})

        assert kept == {"segment": "Starter"}

    @pytest.mark.parametrize(
        ("inputs", "message"),
        [
            ({"segment": "Network", "nope": "x"}, "no input 'nope'"),
            ({"segment": "Solo"}, "not an option"),
            ({"offer": "intro"}, "missing required input 'segment'"),
        ],
    )
    def test_rejects(self, inputs: dict[str, str], message: str) -> None:
        with pytest.raises(ValueError, match=message):
            validate_note_inputs(self._definition(), inputs)


def test_template_substitutes_only_known_placeholders() -> None:
    definition = to_definition("custom.coach", 1, _spec())

    prompt = render_user_prompt(
        definition,
        Transcript(format="txt", content="[00:01] Kurt: Hello"),
        NOW,
        {"segment": "Starter"},
        "Fields: ...",
    )

    assert "Segment: Starter" in prompt
    assert "Offer: not provided" in prompt
    assert "Date: 2026-09-23" in prompt
    assert '{"a": 1}' in prompt
    assert prompt.endswith("[00:01] Kurt: Hello")


class TestRegistry:
    def test_practice_types_sit_beside_the_built_ins(self) -> None:
        repo = InMemoryPracticeNoteTypeRepository()
        _save(repo)
        registry = _registry(repo)

        keys = registry.keys()
        assert "custom.coach" in keys
        assert "soap" in keys
        assert registry.has("custom.coach")

    def test_get_returns_latest_or_a_named_version(self) -> None:
        repo = InMemoryPracticeNoteTypeRepository()
        _save(repo)
        _save(repo, label="Interview Coach v2")
        registry = _registry(repo)

        assert registry.get("custom.coach").version == 2
        assert registry.get("custom.coach", 1).label == "Interview Coach"

    def test_retired_type_is_hidden_but_still_resolves(self) -> None:
        repo = InMemoryPracticeNoteTypeRepository()
        _save(repo)
        repo.retire("custom.coach", NOW)
        registry = _registry(repo)

        assert not registry.has("custom.coach")
        assert all(d.key != "custom.coach" for d in registry.all())
        assert registry.get("custom.coach").label == "Interview Coach"

    def test_saving_again_makes_it_available(self) -> None:
        repo = InMemoryPracticeNoteTypeRepository()
        _save(repo)
        repo.retire("custom.coach", NOW)
        _save(repo)

        assert _registry(repo).has("custom.coach")

    def test_unknown_practice_key_is_not_registered(self) -> None:
        registry = _registry(InMemoryPracticeNoteTypeRepository())

        assert not registry.has("custom.missing")
        with pytest.raises(KeyError):
            registry.get("custom.missing")

    def test_without_a_source_practice_keys_resolve_to_nothing(self) -> None:
        registry = NoteTypeRegistry()

        assert not registry.has("custom.coach")
        with pytest.raises(KeyError):
            registry.get("custom.coach")

    def test_built_ins_cannot_claim_the_practice_namespace(self) -> None:
        definition = to_definition("custom.coach", 1, _spec())

        with pytest.raises(ValueError, match="belong to practices"):
            NoteTypeRegistry().register(definition)


def test_generation_sends_the_floor_inputs_and_template() -> None:
    repo = InMemoryPracticeNoteTypeRepository()
    _save(repo)
    gateway = FakeStructuredLLMGateway(
        responses=[
            StructuredCompletion(
                data={
                    "fix": {"one_thing": "Ask for referrals by minute 12."},
                    "log_row": {"channels": ["email", "fax"]},
                }
            )
        ]
    )
    service = RegistryNoteGenerationService(registry=_registry(repo), llm_gateway=gateway)
    patient = Patient(id="p-1", first_name="Pat", last_name="Lee", created_at=NOW, updated_at=NOW)

    generated = service.generate_note(
        "custom.coach",
        Transcript(format="txt", content="[00:01] Kurt: Hello"),
        patient,
        NOW,
        inputs={"segment": "Prescriber"},
    )

    call = gateway.calls[0]
    assert call["system_prompt"].endswith(GENERATION_FLOOR)
    assert "Segment: Prescriber" in call["user_prompt"]
    assert "one_thing" in call["user_prompt"]
    assert generated.note_type_version == 1
    assert generated.content["log_row"]["channels"] == ["email", "fax"]


class TestRoutes:
    @pytest.fixture
    def repo(self) -> InMemoryPracticeNoteTypeRepository:
        return InMemoryPracticeNoteTypeRepository()

    @pytest.fixture
    def client(self, repo: InMemoryPracticeNoteTypeRepository) -> Any:
        user = SimpleNamespace(id="00000000-0000-0000-0000-000000000001")
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[require_baa_acceptance] = lambda: user
        app.dependency_overrides[get_practice_note_type_repository] = lambda: repo
        app.dependency_overrides[get_registry] = lambda: _registry(repo)
        try:
            yield TestClient(app)
        finally:
            for dependency in (
                get_current_user,
                require_baa_acceptance,
                get_practice_note_type_repository,
                get_registry,
            ):
                app.dependency_overrides.pop(dependency, None)

    def test_save_list_version_retire(self, client: TestClient) -> None:
        first = client.put("/api/note-types/custom/coach", json=COACH_SPEC)
        assert first.status_code == 200, first.text
        assert first.json()["key"] == "custom.coach"
        assert first.json()["version"] == 1
        assert first.json()["inputs"][0]["options"] == ["Network", "Starter", "Prescriber"]

        second = client.put(
            "/api/note-types/custom/coach", json={**COACH_SPEC, "label": "Coach v2"}
        )
        assert second.json()["version"] == 2

        keys = [t["key"] for t in client.get("/api/note-types").json()["note_types"]]
        assert "custom.coach" in keys

        old = client.get("/api/note-types/custom.coach", params={"version": 1})
        assert old.json()["label"] == "Interview Coach"

        retired = client.delete("/api/note-types/custom/coach")
        assert retired.status_code == 200
        keys = [t["key"] for t in client.get("/api/note-types").json()["note_types"]]
        assert "custom.coach" not in keys
        assert client.get("/api/note-types/custom.coach").status_code == 200

    def test_rejects_a_bad_slug(self, client: TestClient) -> None:
        response = client.put("/api/note-types/custom/Bad-Slug", json=COACH_SPEC)

        assert response.status_code == 422

    def test_rejects_an_invalid_definition(self, client: TestClient) -> None:
        response = client.put(
            "/api/note-types/custom/coach", json={**COACH_SPEC, "user_template": "no body"}
        )

        assert response.status_code == 422

    def test_retiring_an_unknown_type_is_404(self, client: TestClient) -> None:
        assert client.delete("/api/note-types/custom/nothing").status_code == 404
