# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The note import keeps its blocking model call off the event loop.

The parse is a synchronous model request that can take minutes when the model
stalls. Run on the event loop it froze every other request on the instance
until it returned; these tests hold the parse open and check the rest of the
app keeps answering, that a stalled parse fails as a retryable 503, and that
the parse thread still sees the request's database session and tenant.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import httpx
import pytest
from app.db import _current_tenant_schema, _request_session
from app.db.middleware import DatabaseSessionMiddleware
from app.main import app
from app.models import Patient
from app.routes import sessions as sessions_routes
from app.services.note_import_service import ParsedImportedNote
from app.services.note_service import NoteService
from app.services.session_service import SessionService

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )

_PARSED = ParsedImportedNote(
    content={
        "subjective": {"client_narrative": "Discussed progress on weekly goals."},
        "objective": {"appearance": "Well groomed."},
        "assessment": {"clinical_impression": "Steady progress."},
        "plan": {"next_steps": ["continue weekly sessions"]},
    },
    session_date=None,
    session_time=None,
)

# Long enough that a blocked event loop is unmistakable, short enough that a
# regression fails in seconds rather than hanging the suite.
_HOLD_SECONDS = 5.0


class _HeldParse:
    """A parse that blocks its thread until released, recording what it saw."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.seen_schema: str | None = None
        self.saw_request_session = False

    def parse_soap_note(self, source_text: str) -> ParsedImportedNote:
        self.seen_schema = _current_tenant_schema.get()
        self.saw_request_session = _request_session.get() is not None
        self.entered.set()
        self.release.wait(_HOLD_SECONDS)
        return _PARSED


@pytest.fixture
def held_parse() -> Iterator[_HeldParse]:
    parse = _HeldParse()
    yield parse
    # Never leave an abandoned worker thread blocked past the test.
    parse.release.set()


@pytest.fixture
def import_client(
    client: Any,
    held_parse: _HeldParse,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> str:
    """Wire the import route to the held parse; return the import URL."""
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Test",
        last_name="Client",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        session_count=0,
        last_session_date=None,
    )
    mock_repo.create(patient, mock_user_id)
    app.dependency_overrides[sessions_routes.get_note_import_service] = lambda: held_parse
    app.dependency_overrides[sessions_routes.get_session_service] = lambda: SessionService(
        mock_session_repo, mock_repo, Mock(), NoteService(mock_notes_repo)
    )
    # Resolve the caller to a practice the way the middleware does in
    # production, so the test can see that tenant inside the parse thread.
    monkeypatch.setattr(
        DatabaseSessionMiddleware,
        "_prepare",
        staticmethod(lambda *_: ("practice_test", None)),
    )
    return f"/api/patients/{patient.id}/sessions/import"


def _note_file() -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("note.txt", b"Chief complaint: Trouble sleeping.", "text/plain")}


def test_other_requests_are_served_while_a_parse_is_running(
    import_client: str, held_parse: _HeldParse
) -> None:
    async def scenario() -> tuple[int, int, bool]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            importing = asyncio.create_task(ac.post(import_client, files=_note_file()))
            while not held_parse.entered.is_set():
                await asyncio.sleep(0.01)

            started = time.monotonic()
            listed = await ac.get("/api/sessions")
            answered_in = time.monotonic() - started
            import_still_running = not importing.done()

            held_parse.release.set()
            imported = await importing
            assert answered_in < 2.0, f"GET took {answered_in:.1f}s behind the parse"
            return listed.status_code, imported.status_code, import_still_running

    listed, imported, import_still_running = asyncio.run(scenario())

    assert listed == 200
    assert import_still_running, "the import finished before the other request was answered"
    assert imported == 201


def test_a_stalled_parse_fails_fast_as_retryable(
    import_client: str,
    held_parse: _HeldParse,
    client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sessions_routes, "IMPORT_PARSE_TIMEOUT_SECONDS", 0.2)

    started = time.monotonic()
    response = client.post(import_client, files=_note_file())
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "IMPORT_PARSE_TIMEOUT"
    assert elapsed < _HOLD_SECONDS / 2, "the route waited for the stalled parse"


def test_the_parse_thread_sees_the_request_tenant_and_session(
    import_client: str, held_parse: _HeldParse, client: Any
) -> None:
    held_parse.release.set()

    response = client.post(import_client, files=_note_file())

    assert response.status_code == 201
    assert held_parse.seen_schema == "practice_test"
    assert held_parse.saw_request_session
