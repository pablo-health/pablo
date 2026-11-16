# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A session date with no offset is compared with a stored, aware one.

The upload dialog sends ``session_date`` from a ``datetime-local`` field, so it
carries no offset, and an imported document's date has none either. The
client's ``last_session_date`` is ``timestamptz`` and reads back aware.
Comparing the two raised ``TypeError``, so every upload after a client's first
returned 500 (PABLO-8vsn). The in-memory repository keeps whatever it was
given, so these tests store an aware ``last_session_date`` the way Postgres
returns it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest
from app.models import Patient, TranscriptFormat, UploadSessionRequest
from app.models.transcript import TranscriptModel
from app.routes import sessions as sessions_routes
from app.services import NoteService, SessionService
from app.settings import Settings

if TYPE_CHECKING:
    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )
    from fastapi.testclient import TestClient

# The client's previous session, as Postgres hands it back: aware.
LAST_SESSION = datetime(2026, 10, 26, 15, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(database_url="postgresql://x:x@localhost:5432/x", environment="development")
    monkeypatch.setattr(sessions_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(sessions_routes, "enqueue", lambda *_a, **_k: None)


@pytest.fixture
def patient_id(mock_repo: InMemoryPatientRepository, mock_user_id: str) -> str:
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Test",
        last_name="Client",
        created_at=now,
        updated_at=now,
        session_count=1,
        last_session_date=LAST_SESSION,
    )
    return mock_repo.create(patient, mock_user_id).id


def _upload(client: TestClient, patient_id: str, session_date: str) -> Any:
    return client.post(
        f"/api/patients/{patient_id}/sessions/upload",
        json={
            "patient_id": patient_id,
            "session_date": session_date,
            "transcript": {"format": "txt", "content": "[00:01] Therapist: Hello"},
        },
    )


def _last_session(repo: InMemoryPatientRepository, patient_id: str, user_id: str) -> datetime:
    patient = repo.get(patient_id, user_id)
    assert patient is not None
    assert patient.last_session_date is not None
    return patient.last_session_date


def test_a_naive_date_uploads_for_a_client_with_an_earlier_session(
    client: TestClient, patient_id: str, mock_repo: InMemoryPatientRepository, mock_user_id: str
) -> None:
    """The dialog's exact shape: no seconds, no offset. It was a 500."""
    response = _upload(client, patient_id, "2026-11-02T10:00")

    assert response.status_code == 202, response.text
    assert _last_session(mock_repo, patient_id, mock_user_id) == datetime(
        2026, 11, 2, 10, 0, tzinfo=UTC
    )


def test_an_older_naive_date_leaves_the_last_session_alone(
    client: TestClient, patient_id: str, mock_repo: InMemoryPatientRepository, mock_user_id: str
) -> None:
    """A back-dated upload still compares, and does not move the date back."""
    response = _upload(client, patient_id, "2026-10-01T10:00")

    assert response.status_code == 202, response.text
    assert _last_session(mock_repo, patient_id, mock_user_id) == LAST_SESSION


def test_a_date_with_an_offset_keeps_its_instant(
    client: TestClient, patient_id: str, mock_repo: InMemoryPatientRepository, mock_user_id: str
) -> None:
    """An offset the caller sent is honoured, not replaced with UTC."""
    response = _upload(client, patient_id, "2026-11-02T10:00:00-05:00")

    assert response.status_code == 202, response.text
    stored = _last_session(mock_repo, patient_id, mock_user_id)
    assert stored == datetime(2026, 11, 2, 15, 0, tzinfo=UTC)
    assert stored.utcoffset() == timedelta(hours=-5)


def test_the_request_model_reads_a_naive_date_as_utc() -> None:
    transcript = TranscriptModel(format=TranscriptFormat.TXT, content="x")
    naive = UploadSessionRequest.model_validate(
        {"patient_id": "p", "session_date": "2026-11-02T10:00", "transcript": transcript}
    )
    eastern = timezone(timedelta(hours=-5))
    aware = UploadSessionRequest(
        patient_id="p",
        session_date=datetime(2026, 11, 2, 10, 0, tzinfo=eastern),
        transcript=transcript,
    )

    assert naive.session_date == datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
    assert naive.session_date.tzinfo is UTC
    assert aware.session_date.tzinfo is eastern


@pytest.mark.parametrize(
    ("document_date", "expected_last"),
    [
        (datetime(2026, 11, 2, 10, 0), datetime(2026, 11, 2, 10, 0, tzinfo=UTC)),
        (datetime(2026, 10, 1, 10, 0), LAST_SESSION),
    ],
    ids=["later", "earlier"],
)
def test_an_imported_note_with_a_naive_date_compares(
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
    patient_id: str,
    document_date: datetime,
    expected_last: datetime,
) -> None:
    """The import path (session_service.py, import_session) is handed a naive
    date by its route, from the form or the document."""
    service = SessionService(mock_session_repo, mock_repo, Mock(), NoteService(mock_notes_repo))

    session, patient, _note = service.import_session(
        patient_id,
        mock_user_id,
        session_date=document_date,
        source_text="original document text",
        note_content={"subjective": "s"},
    )

    assert session.session_date == document_date.replace(tzinfo=UTC)
    assert patient.last_session_date == expected_last
    assert patient.session_count == 2
