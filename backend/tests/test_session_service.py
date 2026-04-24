# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for SessionService business logic."""

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from app.models import (
    FinalizeSessionRequest,
    Patient,
    SessionStatus,
    TherapySession,
    Transcript,
    TranscriptFormat,
    UpdateSessionRatingRequest,
    UploadSessionRequest,
)
from app.models.session import SOAPNote
from app.models.soap_note import SOAPNoteModel
from app.models.transcript import TranscriptModel
from app.repositories import InMemoryPatientRepository, InMemoryTherapySessionRepository
from app.services.note_generation_service import GeneratedNote, NoteGenerationService
from app.services.session_service import (
    InvalidSessionStatusError,
    PatientNotFoundError,
    SessionNotFoundError,
    SessionService,
    SOAPGenerationFailedError,
)


@pytest.fixture
def session_repo() -> InMemoryTherapySessionRepository:
    return InMemoryTherapySessionRepository()


@pytest.fixture
def patient_repo(
    session_repo: InMemoryTherapySessionRepository,
) -> InMemoryPatientRepository:
    return InMemoryPatientRepository(session_repo=session_repo)


@pytest.fixture
def mock_note_service() -> Mock:
    service = Mock(spec=NoteGenerationService)
    soap_note = SOAPNote.from_dict(
        {
            "subjective": "Patient reports anxiety.",
            "objective": "Patient appears nervous.",
            "assessment": "Generalized anxiety disorder.",
            "plan": "Continue weekly therapy.",
        }
    )
    service.generate_note.return_value = GeneratedNote(
        note_type="soap",
        content=soap_note.to_dict(),
        soap_note=soap_note,
    )
    return service


@pytest.fixture
def user_id() -> str:
    return "test-user-123"


@pytest.fixture
def patient(patient_repo: InMemoryPatientRepository, user_id: str) -> Patient:
    p = Patient(
        id=str(uuid.uuid4()),
        user_id=user_id,
        first_name="John",
        last_name="Doe",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        session_count=0,
        last_session_date=None,
    )
    patient_repo.create(p)
    return p


@pytest.fixture
def service(
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
    mock_note_service: Mock,
) -> SessionService:
    return SessionService(session_repo, patient_repo, mock_note_service)


def _make_pending_session(
    session_repo: InMemoryTherapySessionRepository,
    user_id: str,
    patient_id: str,
) -> TherapySession:
    """Helper to create a session in pending_review status."""
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
        session_number=1,
        status=SessionStatus.PENDING_REVIEW,
        transcript=Transcript(format="txt", content="Test content"),
        created_at=datetime.now(UTC),
        soap_note=SOAPNote.from_dict(
            {
                "subjective": "S",
                "objective": "O",
                "assessment": "A",
                "plan": "P",
            }
        ),
    )
    session_repo.create(session)
    return session


def _make_finalized_session(
    session_repo: InMemoryTherapySessionRepository,
    user_id: str,
    patient_id: str,
) -> TherapySession:
    """Helper to create a session in finalized status."""
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
        session_number=1,
        status=SessionStatus.FINALIZED,
        transcript=Transcript(format="txt", content="Test content"),
        created_at=datetime.now(UTC),
        soap_note=SOAPNote.from_dict(
            {
                "subjective": "S",
                "objective": "O",
                "assessment": "A",
                "plan": "P",
            }
        ),
        quality_rating=5,
        finalized_at=datetime.now(UTC),
    )
    session_repo.create(session)
    return session


class TestUploadSession:
    def test_successful_upload(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
    ) -> None:
        request = UploadSessionRequest(
            patient_id=patient.id,
            session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
            transcript=TranscriptModel(format=TranscriptFormat.TXT, content="Test transcript"),
        )

        session, returned_patient = service.upload_session(patient.id, user_id, request)

        assert session.status == SessionStatus.PENDING_REVIEW
        assert session.soap_note is not None
        assert session.patient_id == patient.id
        assert session.user_id == user_id
        assert returned_patient.session_count == 1
        assert returned_patient.last_session_date == datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

    def test_patient_not_found(
        self,
        service: SessionService,
        user_id: str,
    ) -> None:
        request = UploadSessionRequest(
            patient_id="nonexistent-patient",
            session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
            transcript=TranscriptModel(format=TranscriptFormat.TXT, content="Test transcript"),
        )

        with pytest.raises(PatientNotFoundError):
            service.upload_session("nonexistent-patient", user_id, request)

    def test_soap_generation_failure(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        mock_note_service: Mock,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        mock_note_service.generate_note.side_effect = RuntimeError("LLM error")

        request = UploadSessionRequest(
            patient_id=patient.id,
            session_date=datetime.fromisoformat("2026-01-15T10:00:00+00:00"),
            transcript=TranscriptModel(format=TranscriptFormat.TXT, content="Test transcript"),
        )

        with pytest.raises(SOAPGenerationFailedError):
            service.upload_session(patient.id, user_id, request)

        # Verify session was marked as failed
        sessions, _ = session_repo.list_by_user(user_id)
        failed = [s for s in sessions if s.status == SessionStatus.FAILED]
        assert len(failed) == 1

    def test_patient_metadata_updated(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        patient_repo: InMemoryPatientRepository,
    ) -> None:
        request = UploadSessionRequest(
            patient_id=patient.id,
            session_date=datetime.fromisoformat("2026-02-01T10:00:00+00:00"),
            transcript=TranscriptModel(format=TranscriptFormat.TXT, content="Test"),
        )

        service.upload_session(patient.id, user_id, request)

        updated = patient_repo.get(patient.id, user_id)
        assert updated is not None
        assert updated.session_count == 1
        assert updated.last_session_date == datetime(2026, 2, 1, 10, 0, tzinfo=UTC)


class TestFinalizeSession:
    def test_successful_finalization(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _make_pending_session(session_repo, user_id, patient.id)

        request = FinalizeSessionRequest(quality_rating=5)
        result_session, _result_patient = service.finalize_session(session.id, user_id, request)

        assert result_session.status == SessionStatus.FINALIZED
        assert result_session.quality_rating == 5
        assert result_session.finalized_at is not None

    def test_session_not_found(
        self,
        service: SessionService,
        user_id: str,
    ) -> None:
        request = FinalizeSessionRequest(quality_rating=5)

        with pytest.raises(SessionNotFoundError):
            service.finalize_session("nonexistent", user_id, request)

    def test_wrong_status(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _make_finalized_session(session_repo, user_id, patient.id)

        request = FinalizeSessionRequest(quality_rating=5)

        with pytest.raises(InvalidSessionStatusError) as exc_info:
            service.finalize_session(session.id, user_id, request)

        assert exc_info.value.current_status == SessionStatus.FINALIZED

    def test_low_rating_with_reason_accepted(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _make_pending_session(session_repo, user_id, patient.id)

        request = FinalizeSessionRequest(
            quality_rating=2,
            quality_rating_reason="Needs improvement",
        )
        result, _ = service.finalize_session(session.id, user_id, request)

        assert result.quality_rating == 2
        assert result.quality_rating_reason == "Needs improvement"

    def test_edited_soap_note_saved(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _make_pending_session(session_repo, user_id, patient.id)

        request = FinalizeSessionRequest(
            quality_rating=5,
            soap_note_edited=SOAPNoteModel(
                subjective="Edited S",
                objective="Edited O",
                assessment="Edited A",
                plan="Edited P",
            ),
        )
        result, _ = service.finalize_session(session.id, user_id, request)

        assert result.soap_note_edited is not None


class TestUpdateRating:
    def test_successful_update(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _make_finalized_session(session_repo, user_id, patient.id)

        request = UpdateSessionRatingRequest(
            quality_rating=3,
            quality_rating_reason="Reassessed quality",
        )
        result, _, old_rating = service.update_rating(session.id, user_id, request)

        assert result.quality_rating == 3
        assert old_rating == 5

    def test_session_not_found(
        self,
        service: SessionService,
        user_id: str,
    ) -> None:
        request = UpdateSessionRatingRequest(quality_rating=4)

        with pytest.raises(SessionNotFoundError):
            service.update_rating("nonexistent", user_id, request)

    def test_wrong_status(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _make_pending_session(session_repo, user_id, patient.id)

        request = UpdateSessionRatingRequest(quality_rating=4)

        with pytest.raises(InvalidSessionStatusError):
            service.update_rating(session.id, user_id, request)

    def test_low_rating_with_feedback_accepted(
        self,
        service: SessionService,
        patient: Patient,
        user_id: str,
        session_repo: InMemoryTherapySessionRepository,
    ) -> None:
        session = _make_finalized_session(session_repo, user_id, patient.id)

        request = UpdateSessionRatingRequest(
            quality_rating=1,
            quality_rating_reason="Reconsidered quality",
        )
        result, _, old_rating = service.update_rating(session.id, user_id, request)

        assert result.quality_rating == 1
        assert old_rating == 5
