# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A note import whose model never answers ends in a retryable 503, in time."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from app.main import app
from app.reliability import LLM_REQUEST
from app.routes.sessions import get_note_import_service
from app.services import structured_llm_gateway
from app.services.note_import_service import NoteImportService
from app.services.structured_llm_gateway import GeminiStructuredLLMGateway
from fastapi.testclient import TestClient  # noqa: TC002 — runtime fixture type

_MESSAGE = "We couldn't read that note just now. Please try again in a minute."
_SECONDS_PER_FAILED_ATTEMPT = 54.0


class _Clock:
    now = 1000.0

    def monotonic(self) -> float:
        return self.now


class _Failing504Models:
    """Every attempt spends most of its 55 s bound, then the provider answers 504."""

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.calls = 0

    def generate_content(self, **_kwargs: Any) -> Any:
        self.calls += 1
        self.clock.now += _SECONDS_PER_FAILED_ATTEMPT
        request = httpx.Request("POST", "https://example.test")
        response = httpx.Response(504, request=request)
        raise httpx.HTTPStatusError("DEADLINE_EXCEEDED", request=request, response=response)


@pytest.fixture
def failing_models(monkeypatch: pytest.MonkeyPatch) -> tuple[_Failing504Models, _Clock]:
    clock = _Clock()
    fake_time = SimpleNamespace(monotonic=clock.monotonic, sleep=lambda _seconds: None)
    monkeypatch.setattr("app.reliability.retry.time", fake_time)
    monkeypatch.setattr(structured_llm_gateway, "time", fake_time)
    monkeypatch.setattr("app.services.note_import_service.monotonic", clock.monotonic)
    # The shared client fixture leaves its own session open; the route's
    # release_db_connection() is not what this test is about.
    monkeypatch.setattr("app.db.assert_no_held_db_connection", lambda _context="": None)
    models = _Failing504Models(clock)
    gateway = GeminiStructuredLLMGateway()
    gateway._client = SimpleNamespace(models=models)  # test double for the genai client
    app.dependency_overrides[get_note_import_service] = lambda: NoteImportService(
        llm_gateway=gateway
    )
    yield models, clock
    app.dependency_overrides.pop(get_note_import_service, None)


def test_import_that_fails_on_both_attempts_is_a_retryable_503(
    client: TestClient, failing_models: tuple[_Failing504Models, _Clock]
) -> None:
    models, clock = failing_models
    started = clock.now

    response = client.post(
        "/api/patients/p-1/sessions/import",
        files={
            "file": (
                "note.txt",
                b"S: slept badly\nO: tired\nA: low mood\nP: follow up",
                "text/plain",
            )
        },
    )

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "note_import_unavailable"
    assert error["message"] == _MESSAGE
    assert models.calls == LLM_REQUEST.max_attempts
    assert clock.now - started <= 125.0
