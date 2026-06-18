# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Recording → note completion path for the desktop capture flow.

The desktop app records a session and drives it to a finished SOAP note
through two server-side stages:

1. ``POST /api/sessions/{id}/upload-audio`` accepts the dual-channel
   audio and moves the session to ``transcribing``. Transcription runs
   asynchronously (GCP Batch for the self-hosted Whisper provider, a
   polling task for AssemblyAI), so the upload endpoint only owns the
   *contract*: accept the bytes, validate the media type, hand off, and
   report ``transcribing``. It deliberately does not wait for a
   transcript.

2. When transcription finishes, the transcript is posted to
   ``POST /api/sessions/{id}/transcript``, which persists it, returns
   ``202``, and hands SOAP generation to the Cloud Tasks worker
   (``SessionService.generate_session_note``). The worker lands the
   session in ``pending_review`` with the note embedded under
   ``GET /api/sessions/{id}``. We assert the route's async contract, then
   run ``generate_session_note`` directly with a deterministic note
   generator instead of a real model — no network, no GPU, stable
   assertions.

Splitting the proof this way keeps it deterministic: stage 1 asserts the
upload contract (including the revert-on-enqueue-failure guard), stage 2
asserts that a completed transcript yields a 202 and that generation then
produces a four-section SOAP note the client can read back. Note *quality*
is an eval concern, not a unit-test one.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from app.models import Patient, SessionStatus, TherapySession, Transcript
from app.repositories import (
    InMemoryNotesRepository,
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
)
from app.services import NoteService, SessionService, SOAPGenerationFailedError
from app.services.note_generation_service import (
    GeneratedNote,
    MockNoteGenerationService,
    NoteGenerationService,
)
from app.settings import Settings

_AUDIO = b"\x00\x01" * 1024  # arbitrary non-empty payload; never transcribed


def _seed_patient(patient_repo: InMemoryPatientRepository, user_id: str) -> Patient:
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Jordan",
        last_name="Rivera",
        diagnosis="Generalized anxiety disorder",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        session_count=0,
    )
    patient_repo.create(patient, user_id)
    return patient


def _seed_session(
    session_repo: InMemoryTherapySessionRepository,
    user_id: str,
    patient_id: str,
    status: str,
) -> TherapySession:
    now = datetime.now(UTC)
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        session_date=now,
        session_number=1,
        status=status,
        transcript=Transcript(format="txt", content=""),
        created_at=now,
        updated_at=now,
    )
    return session_repo.create(session)


def _transcription_settings(*, enabled: bool = True) -> Settings:
    """Dev settings with the async transcription contract switched on.

    ``environment=development`` selects ``MockTranscriptionQueueService``
    inside the route, so the enqueue is a no-op and the session simply
    parks in ``transcribing`` — exactly the async hand-off we want to
    assert without standing up a real queue.
    """
    return Settings(
        environment="development",
        transcription_enabled=enabled,
        transcription_provider="whisper",
    )


class _FailingNoteGenerationService(NoteGenerationService):
    """Stand-in for a model/pipeline failure during completion."""

    def generate_note(self, note_type, transcript, patient, session_date) -> GeneratedNote:  # type: ignore[no-untyped-def]
        raise RuntimeError("note generation blew up")


# Zero-arg factories for ``app.dependency_overrides``: FastAPI introspects
# the override's signature, so we can't hand it a class whose ``__init__``
# takes a non-Pydantic argument (``MockNoteGenerationService(registry=...)``).
def _deterministic_note_generator() -> NoteGenerationService:
    return MockNoteGenerationService()


def _failing_note_generator() -> NoteGenerationService:
    return _FailingNoteGenerationService()


# --- Stage 1: upload-audio contract -------------------------------------


def test_upload_audio_accepts_dual_channel_and_parks_in_transcribing(
    client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    session = _seed_session(
        mock_session_repo, mock_user_id, patient.id, SessionStatus.RECORDING_COMPLETE
    )

    with patch("app.routes.sessions.get_settings", return_value=_transcription_settings()):
        resp = client.post(
            f"/api/sessions/{session.id}/upload-audio",
            files={
                "therapist_audio": ("therapist.wav", _AUDIO, "audio/wav"),
                "client_audio": ("client.wav", _AUDIO, "audio/wav"),
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == SessionStatus.TRANSCRIBING
    assert body["provider"] == "whisper"
    assert mock_session_repo.get(session.id, mock_user_id).status == SessionStatus.TRANSCRIBING


def test_upload_audio_rejects_unsupported_media_type(
    client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    session = _seed_session(
        mock_session_repo, mock_user_id, patient.id, SessionStatus.RECORDING_COMPLETE
    )

    with patch("app.routes.sessions.get_settings", return_value=_transcription_settings()):
        resp = client.post(
            f"/api/sessions/{session.id}/upload-audio",
            files={
                "therapist_audio": ("therapist.txt", b"not audio", "text/plain"),
                "client_audio": ("client.wav", _AUDIO, "audio/wav"),
            },
        )

    assert resp.status_code == 415, resp.text


def test_upload_audio_returns_501_when_transcription_disabled(
    client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    session = _seed_session(
        mock_session_repo, mock_user_id, patient.id, SessionStatus.RECORDING_COMPLETE
    )

    with patch(
        "app.routes.sessions.get_settings",
        return_value=_transcription_settings(enabled=False),
    ):
        resp = client.post(
            f"/api/sessions/{session.id}/upload-audio",
            files={
                "therapist_audio": ("therapist.wav", _AUDIO, "audio/wav"),
                "client_audio": ("client.wav", _AUDIO, "audio/wav"),
            },
        )

    assert resp.status_code == 501, resp.text


def test_upload_audio_enqueue_failure_reverts_to_recording_complete(
    client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_user_id: str,
) -> None:
    """A failed hand-off must not strand the session in ``transcribing``.

    There is no poller for a session that never made it onto the queue,
    so the route reverts to ``recording_complete`` and surfaces a 5xx so
    the client retries the upload.
    """
    patient = _seed_patient(mock_repo, mock_user_id)
    session = _seed_session(
        mock_session_repo, mock_user_id, patient.id, SessionStatus.RECORDING_COMPLETE
    )

    class _BoomQueue:
        def upload_audio(self, *_a, **_k) -> str:  # type: ignore[no-untyped-def]
            return "gs://bucket/object"

        def enqueue_transcription(self, *_a, **_k) -> None:  # type: ignore[no-untyped-def]
            raise RuntimeError("queue unavailable")

    with (
        patch("app.routes.sessions.get_settings", return_value=_transcription_settings()),
        patch("app.routes.sessions.MockTranscriptionQueueService", _BoomQueue),
    ):
        resp = client.post(
            f"/api/sessions/{session.id}/upload-audio",
            files={
                "therapist_audio": ("therapist.wav", _AUDIO, "audio/wav"),
                "client_audio": ("client.wav", _AUDIO, "audio/wav"),
            },
        )

    assert resp.status_code >= 500, resp.text
    assert (
        mock_session_repo.get(session.id, mock_user_id).status == SessionStatus.RECORDING_COMPLETE
    )


# --- Stage 2: transcription completion → SOAP → pending_review ----------


def test_completed_transcript_yields_pending_review_with_four_section_soap(
    client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    session = _seed_session(
        mock_session_repo, mock_user_id, patient.id, SessionStatus.RECORDING_COMPLETE
    )

    # The route persists the transcript and returns 202 — generation is handed
    # to the Cloud Tasks worker, so no note is produced inline.
    complete = client.post(
        f"/api/sessions/{session.id}/transcript",
        json={"format": "txt", "content": "Therapist: ... Client: ..."},
    )
    assert complete.status_code == 202, complete.text
    assert complete.json()["status"] == SessionStatus.PROCESSING

    # Run the worker's generation step (SessionService.generate_session_note,
    # exactly what the Cloud Tasks worker invokes) and assert the produced note.
    service = SessionService(
        mock_session_repo, mock_repo, _deterministic_note_generator(), NoteService(mock_notes_repo)
    )
    service.generate_session_note(session.id, mock_user_id)

    fetched = client.get(f"/api/sessions/{session.id}")
    assert fetched.status_code == 200, fetched.text
    payload = fetched.json()

    assert payload["status"] == SessionStatus.PENDING_REVIEW
    note = payload["note"]
    assert note is not None, "completed session must embed its note"
    content = note["content"]
    for section in ("subjective", "objective", "assessment", "plan"):
        assert section in content, f"SOAP note missing {section!r}"
        assert content[section], f"SOAP section {section!r} is empty"


def test_note_generation_failure_marks_session_failed(
    client,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_repo: InMemoryPatientRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> None:
    patient = _seed_patient(mock_repo, mock_user_id)
    session = _seed_session(
        mock_session_repo, mock_user_id, patient.id, SessionStatus.RECORDING_COMPLETE
    )

    # Upload returns 202; the failure happens in the worker, not on the request.
    complete = client.post(
        f"/api/sessions/{session.id}/transcript",
        json={"format": "txt", "content": "Therapist: ... Client: ..."},
    )
    assert complete.status_code == 202, complete.text

    # The worker's generation raises and durably marks the session FAILED.
    service = SessionService(
        mock_session_repo, mock_repo, _failing_note_generator(), NoteService(mock_notes_repo)
    )
    with pytest.raises(SOAPGenerationFailedError):
        service.generate_session_note(session.id, mock_user_id)

    fetched = client.get(f"/api/sessions/{session.id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["status"] == SessionStatus.FAILED
