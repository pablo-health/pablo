# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A transcript upload drafts the note type the clinician picked.

The upload remembers the requested type the way a scheduled session does, on
an empty note row, and the drafting worker reads it from there. These pin
that contract at the HTTP layer and through the worker's generation step, so
an upload can't quietly fall back to SOAP, and a type the practice doesn't
have is refused before a session is created.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.models import Patient, SessionStatus
from app.notes import NoteTypeAuthorizer, get_note_type_authorizer
from app.routes import sessions as sessions_routes
from app.services import NoteService, SessionService
from app.services.note_generation_service import GeneratedNote, MockNoteGenerationService
from app.settings import Settings

if TYPE_CHECKING:
    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )
    from fastapi.testclient import TestClient

FOLLOW_UP = "psychiatric_follow_up"
IN_OFFICE = {"place_of_service": "In office"}


class _DenyAllNoteTypes(NoteTypeAuthorizer):
    def is_allowed(self, user: object, note_type: str) -> bool:
        return False


class _RecordingGenerator(MockNoteGenerationService):
    """The deterministic drafter, noting which type it was asked for."""

    def __init__(self) -> None:
        super().__init__()
        self.asked: list[str] = []
        self.inputs: list[Any] = []

    def generate_note(self, note_type: str, *args: Any, **kwargs: Any) -> GeneratedNote:
        self.asked.append(note_type)
        self.inputs.append(kwargs.get("inputs"))
        return super().generate_note(note_type, *args, **kwargs)


@pytest.fixture(autouse=True)
def _no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(database_url="postgresql://x:x@localhost:5432/x", environment="development")
    monkeypatch.setattr(sessions_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(sessions_routes, "enqueue", lambda *_a, **_k: None)


@pytest.fixture
def patient_id(mock_repo: InMemoryPatientRepository, mock_user_id: str) -> str:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()), first_name="Test", last_name="Client", created_at=now, updated_at=now
    )
    return mock_repo.create(patient, mock_user_id).id


def _upload(client: TestClient, patient_id: str, **extra: object) -> Any:
    return client.post(
        f"/api/patients/{patient_id}/sessions/upload",
        json={
            "patient_id": patient_id,
            "session_date": "2026-10-05T14:00:00",
            "transcript": {"format": "txt", "content": "[00:01] Therapist: Hello"},
            **extra,
        },
    )


def _draft(
    session_id: str,
    user_id: str,
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
    notes_repo: InMemoryNotesRepository,
) -> tuple[_RecordingGenerator, str]:
    """Run the worker's generation step; return the drafter and the note's type."""
    generator = _RecordingGenerator()
    service = SessionService(session_repo, patient_repo, generator, NoteService(notes_repo))
    _session, _patient, note = service.generate_session_note(session_id, user_id)
    return generator, note.note_type


def test_an_upload_with_a_note_type_drafts_that_type(
    client: TestClient,
    patient_id: str,
    mock_user_id: str,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
) -> None:
    """Catches the type or its inputs being dropped between the upload and the
    worker, which would draft a SOAP note for a clinician who asked for a
    follow-up, or a follow-up that forgot where the visit took place."""
    response = _upload(client, patient_id, note_type=FOLLOW_UP, note_inputs=IN_OFFICE)

    assert response.status_code == 202, response.text
    session_id = response.json()["id"]
    waiting = mock_notes_repo.get_by_session_id(session_id)
    assert waiting is not None
    assert waiting.note_type == FOLLOW_UP
    assert waiting.note_inputs == IN_OFFICE

    generator, drafted = _draft(
        session_id, mock_user_id, mock_session_repo, mock_repo, mock_notes_repo
    )
    assert generator.asked == [FOLLOW_UP]
    assert generator.inputs == [IN_OFFICE]
    assert drafted == FOLLOW_UP
    session = mock_session_repo.get(session_id, mock_user_id)
    assert session is not None
    assert session.status == SessionStatus.PENDING_REVIEW


def test_an_upload_without_a_note_type_still_drafts_soap(
    client: TestClient,
    patient_id: str,
    mock_user_id: str,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
) -> None:
    """Catches the new field changing the default for every caller that
    never sends one: no note row is written ahead, and the draft is SOAP."""
    response = _upload(client, patient_id)

    assert response.status_code == 202, response.text
    session_id = response.json()["id"]
    assert mock_notes_repo.get_by_session_id(session_id) is None

    generator, drafted = _draft(
        session_id, mock_user_id, mock_session_repo, mock_repo, mock_notes_repo
    )
    assert generator.asked == ["soap"]
    assert drafted == "soap"


def test_an_unknown_note_type_is_refused_like_the_schedule_path(
    client: TestClient,
    patient_id: str,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> None:
    """Catches a typo'd or retired key being accepted and failing later in the
    worker, after the session already counts against the client."""
    uploaded = _upload(client, patient_id, note_type="not-a-real-type")
    scheduled = client.post(
        "/api/sessions/schedule",
        json={
            "patient_id": patient_id,
            "scheduled_at": "2026-10-05T14:00:00Z",
            "note_type": "not-a-real-type",
        },
    )

    assert uploaded.status_code == 400, uploaded.text
    assert scheduled.status_code == 400, scheduled.text
    assert uploaded.json()["error"]["code"] == scheduled.json()["error"]["code"]
    assert uploaded.json()["error"]["code"] == "INVALID_NOTE_TYPE"
    assert mock_session_repo.list_by_user(mock_user_id)[1] == 0


def test_a_type_missing_a_required_input_is_refused(
    client: TestClient,
    patient_id: str,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> None:
    """Catches the upload skipping the input check the schedule path makes, so a
    follow-up is never queued without its place of service."""
    response = _upload(client, patient_id, note_type=FOLLOW_UP)
    scheduled = client.post(
        "/api/sessions/schedule",
        json={
            "patient_id": patient_id,
            "scheduled_at": "2026-10-05T14:00:00Z",
            "note_type": FOLLOW_UP,
        },
    )

    assert response.status_code == 400, response.text
    assert scheduled.status_code == 400, scheduled.text
    assert response.json() == scheduled.json()
    assert response.json()["error"]["code"] == "INVALID_NOTE_TYPE"
    assert "place_of_service" in response.json()["error"]["message"]
    assert mock_session_repo.list_by_user(mock_user_id)[1] == 0


def test_a_hand_written_note_type_is_refused(
    client: TestClient,
    patient_id: str,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> None:
    """Catches a restricted (psychotherapy) note being queued for a draft the
    drafter will always refuse, leaving a failed session behind."""
    response = _upload(client, patient_id, note_type="psychotherapy")

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "INVALID_NOTE_TYPE"
    assert mock_session_repo.list_by_user(mock_user_id)[1] == 0


def test_a_locked_note_type_is_refused_before_a_session_exists(
    client: TestClient,
    patient_id: str,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_user_id: str,
) -> None:
    """Catches the upload skipping the authorizer the schedule path applies."""
    app.dependency_overrides[get_note_type_authorizer] = _DenyAllNoteTypes
    try:
        locked = _upload(client, patient_id, note_type=FOLLOW_UP, note_inputs=IN_OFFICE)
        default = _upload(client, patient_id)
    finally:
        app.dependency_overrides.pop(get_note_type_authorizer, None)

    assert locked.status_code == 403, locked.text
    assert default.status_code == 202, default.text
    assert mock_session_repo.list_by_user(mock_user_id)[1] == 1
