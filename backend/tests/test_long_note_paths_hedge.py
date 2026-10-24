# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The two long drafting paths race a stalled primary at its stall threshold.

``POST /api/note-types/preview`` drafts while the clinician waits, and the
``generate-soap`` job drafts a session's note off the upload request. Both
take their generator from ``get_note_generation_service``, so both should
start the fallback beside a primary that is still running after the
``note_generation`` entry in ``ai_hedge_delays_ms``, and answer with
whichever finishes first. Nothing here passes a gateway in: the generator
is the one the app builds, from settings, as in production. The models are
``test:`` ids; the primary holds every call until the test ends.
"""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from app.auth.service import get_current_user, require_baa_acceptance
from app.main import app
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.routes.note_types import get_registry
from app.routes.notes import get_note_generation_service
from app.routes.sessions import generate_soap_job
from app.services import structured_llm_gateway
from app.services.audit_service import AuditService, get_audit_service
from app.services.structured_llm_gateway import (
    StructuredCompletion,
    StructuredLLMGateway,
    register_structured_llm_provider,
)
from app.settings import get_settings
from fastapi.dependencies.utils import get_dependant
from fastapi.testclient import TestClient

from .test_generation_fallback import FALLBACK, NOW, PATIENT, PRIMARY, TRANSCRIPT, WRITTEN, instance

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from app.models import User
    from app.repositories.audit import InMemoryAuditRepository
    from fastapi.dependencies.models import Dependant

#: The stall threshold under test, in ms: short so the test is quick, and
#: far below how long the primary is held.
HEDGE_MS = 200
#: How long the primary holds a call unless released first.
STALL_SECONDS = 10.0
#: What the primary would have written, had anyone waited for it.
PRIMARY_TEXT = "The primary answered after its stall"


class StalledPrimary(StructuredLLMGateway):
    """Serves ``test:`` models: the primary holds each call, the fallback answers at once."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.first_start: dict[str, float] = {}
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def complete_structured(
        self, *, model: str, response_schema: dict[str, Any], **_: Any
    ) -> StructuredCompletion:
        with self._lock:
            self.first_start.setdefault(model, time.monotonic())
            self.calls.append(model)
        if model == PRIMARY:
            self.release.wait(STALL_SECONDS)
            return StructuredCompletion(data=instance(response_schema, PRIMARY_TEXT))
        return StructuredCompletion(data=instance(response_schema))


@pytest.fixture
def stalled(monkeypatch: pytest.MonkeyPatch) -> Iterator[StalledPrimary]:
    """Configure note drafting as a deployment would, on a stalling primary."""
    monkeypatch.delenv("NOTE_GENERATION_BASE_URL", raising=False)
    monkeypatch.setenv("AI_MODELS", json.dumps({"note_generation": PRIMARY}))
    monkeypatch.setenv("AI_FALLBACKS", json.dumps({"note_generation": FALLBACK}))
    monkeypatch.setenv("AI_HEDGE_DELAYS_MS", json.dumps({"note_generation": HEDGE_MS}))
    get_settings.cache_clear()
    provider = StalledPrimary()
    register_structured_llm_provider("test", lambda: provider)
    try:
        yield provider
    finally:
        provider.release.set()
        structured_llm_gateway._registered_providers.pop("test", None)
        get_settings.cache_clear()


def _assert_hedged(provider: StalledPrimary, began: float, ended: float) -> None:
    """The fallback joined at the threshold, and nobody waited out the stall."""
    assert PRIMARY in provider.first_start, "the primary is asked first"
    joined = provider.first_start[FALLBACK] - provider.first_start[PRIMARY]
    assert HEDGE_MS / 1000 * 0.75 <= joined < STALL_SECONDS / 2
    assert ended - began < STALL_SECONDS / 2


def _dependency_calls(dependant: Dependant) -> set[Callable[..., Any]]:
    calls: set[Callable[..., Any]] = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            calls.add(sub.call)
        calls |= _dependency_calls(sub)
    return calls


class TestNoteTypePreview:
    @pytest.fixture
    def client(
        self, stalled: StalledPrimary, audit_repo: InMemoryAuditRepository, mock_user: User
    ) -> Iterator[TestClient]:
        # The generator is deliberately not overridden: the route builds its own.
        registry = NoteTypeRegistry()
        register_builtin_note_types(registry)
        overrides: dict[Callable[..., Any], Callable[..., Any]] = {
            get_current_user: lambda: mock_user,
            require_baa_acceptance: lambda: mock_user,
            get_registry: lambda: registry,
            get_audit_service: lambda: AuditService(audit_repo),
        }
        app.dependency_overrides.update(overrides)
        try:
            yield TestClient(app)
        finally:
            for dependency in overrides:
                app.dependency_overrides.pop(dependency, None)

    def test_a_stalled_primary_is_answered_by_the_fallback_after_the_threshold(
        self, client: TestClient, stalled: StalledPrimary
    ) -> None:
        began = time.monotonic()
        response = client.post(
            "/api/note-types/preview",
            json={
                "key": "narrative",
                "transcript": {"format": "txt", "content": TRANSCRIPT.content},
            },
        )
        ended = time.monotonic()

        assert response.status_code == 200, response.text
        assert WRITTEN in json.dumps(response.json()["sections"])
        assert PRIMARY_TEXT not in response.text
        _assert_hedged(stalled, began, ended)


class TestGenerateSoapJob:
    def test_the_job_drafts_with_the_apps_note_generator(self) -> None:
        """The job's session service is handed ``get_note_generation_service``,
        the same generator the preview drafts with."""
        dependant = get_dependant(path="/api/internal/jobs/generate-soap", call=generate_soap_job)
        assert get_note_generation_service in _dependency_calls(dependant)

    def test_a_stalled_primary_is_answered_by_the_fallback_after_the_threshold(
        self, stalled: StalledPrimary
    ) -> None:
        generator = get_note_generation_service()

        began = time.monotonic()
        generated = generator.generate_note("soap", TRANSCRIPT, PATIENT, NOW)
        ended = time.monotonic()

        assert generated.soap_note is not None
        assert generated.soap_note.subjective.chief_complaint.text == WRITTEN
        _assert_hedged(stalled, began, ended)
