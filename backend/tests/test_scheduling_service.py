# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for session scheduling, status transitions, metadata, and transcript upload."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from app.models import (
    Patient,
    ScheduleSessionRequest,
    SessionStatus,
    TherapySession,
    Transcript,
    UpdateSessionMetadataRequest,
    UpdateSessionStatusRequest,
    UploadTranscriptToSessionRequest,
    UserPreferences,
    VideoPlatform,
)
from app.models.session import SOAPNote
from app.repositories import (
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
    InMemoryUserRepository,
)
from app.services.session_service import (
    InvalidSessionStatusError,
    InvalidStatusTransitionError,
    PatientNotFoundError,
    SessionAlreadyInStatusError,
    SessionInTerminalStatusError,
    SessionNotFoundError,
    SessionService,
)
from app.services.soap_generation_service import SOAPGenerationService


@pytest.fixture
def session_repo() -> InMemoryTherapySessionRepository:
    return InMemoryTherapySessionRepository()

@pytest.fixture
def patient_repo(
    session_repo: InMemoryTherapySessionRepository,
) -> InMemoryPatientRepository:
    return InMemoryPatientRepository(session_repo=session_repo)

@pytest.fixture
def mock_soap_service() -> Mock:
    service = Mock(spec=SOAPGenerationService)
    service.generate_soap_note.return_value = SOAPNote.from_dict(
        {
            "subjective": "Patient reports anxiety.",
            "objective": "Patient appears nervous.",
            "assessment": "Generalized anxiety disorder.",
            "plan": "Continue weekly therapy.",
        }
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
        first_name="Jane",
        last_name="Smith",
        created_at=datetime.now(UTC).isoformat(),
        updated_at=datetime.now(UTC).isoformat(),
        session_count=0,
    )
    patient_repo.create(p)
    return p

@pytest.fixture
def service(
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
    mock_soap_service: Mock,
) -> SessionService:
    return SessionService(session_repo, patient_repo, mock_soap_service)

def _make_session(
    session_repo: InMemoryTherapySessionRepository,
    user_id: str,
    patient_id: str,
    status: str = SessionStatus.SCHEDULED,
    scheduled_at: str | None = None,
) -> TherapySession:
    """Helper to create a session in a given status."""
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    session = TherapySession(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        session_date=scheduled_at or now,
        session_number=1,
        status=status,
        transcript=Transcript(format="txt", content=""),
        created_at=now,
        scheduled_at=scheduled_at or now,
        updated_at=now,
    )
    if status == SessionStatus.IN_PROGRESS:
        session.started_at = now
    if status == SessionStatus.RECORDING_COMPLETE:
        session.started_at = now
        session.ended_at = now
    return session_repo.create(session)

# --- Schedule session tests ---

class TestScheduleSession:
    def test_creates_scheduled_session(
        self, service: SessionService, user_id: str, patient: Patient
    ) -> None:
        req = ScheduleSessionRequest(
            patient_id=patient.id,
            scheduled_at="2026-03-07T14:00:00Z",
            duration_minutes=50,
            video_link="https://zoom.us/j/123",
            video_platform=VideoPlatform.ZOOM,
        )
        session, returned_patient = service.schedule_session(user_id, req)

        assert session.status == SessionStatus.SCHEDULED
        assert session.patient_id == patient.id
        assert session.scheduled_at == "2026-03-07T14:00:00Z"
        assert session.video_link == "https://zoom.us/j/123"
        assert session.video_platform == "zoom"
        assert session.duration_minutes == 50
        assert session.session_type == "individual"
        assert session.source == "companion"
        assert returned_patient.id == patient.id

    def test_patient_not_found(self, service: SessionService, user_id: str) -> None:
        req = ScheduleSessionRequest(
            patient_id="nonexistent",
            scheduled_at="2026-03-07T14:00:00Z",
        )
        with pytest.raises(PatientNotFoundError):
            service.schedule_session(user_id, req)

# --- Status transition tests ---

class TestTransitionStatus:
    def test_scheduled_to_in_progress(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        req = UpdateSessionStatusRequest(status=SessionStatus.IN_PROGRESS)

        updated, _ = service.transition_status(session.id, user_id, req)

        assert updated.status == SessionStatus.IN_PROGRESS
        assert updated.started_at is not None

    def test_in_progress_to_recording_complete(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.IN_PROGRESS)
        req = UpdateSessionStatusRequest(status=SessionStatus.RECORDING_COMPLETE)

        updated, _ = service.transition_status(session.id, user_id, req)

        assert updated.status == SessionStatus.RECORDING_COMPLETE
        assert updated.ended_at is not None

    def test_cancel_from_scheduled(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        req = UpdateSessionStatusRequest(status=SessionStatus.CANCELLED)

        updated, _ = service.transition_status(session.id, user_id, req)

        assert updated.status == SessionStatus.CANCELLED
        # ended_at not set because started_at was None
        assert updated.ended_at is None

    def test_cancel_from_in_progress_sets_ended_at(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.IN_PROGRESS)
        req = UpdateSessionStatusRequest(status=SessionStatus.CANCELLED)

        updated, _ = service.transition_status(session.id, user_id, req)

        assert updated.status == SessionStatus.CANCELLED
        assert updated.ended_at is not None

    def test_invalid_transition_raises(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        req = UpdateSessionStatusRequest(status=SessionStatus.FINALIZED)

        with pytest.raises(InvalidStatusTransitionError) as exc_info:
            service.transition_status(session.id, user_id, req)
        assert exc_info.value.current == SessionStatus.SCHEDULED
        assert exc_info.value.target == SessionStatus.FINALIZED

    def test_already_in_status_raises_409(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        req = UpdateSessionStatusRequest(status=SessionStatus.SCHEDULED)

        with pytest.raises(SessionAlreadyInStatusError):
            service.transition_status(session.id, user_id, req)

    def test_session_not_found(self, service: SessionService, user_id: str) -> None:
        req = UpdateSessionStatusRequest(status=SessionStatus.IN_PROGRESS)
        with pytest.raises(SessionNotFoundError):
            service.transition_status("nonexistent", user_id, req)

# --- Metadata update tests ---

class TestUpdateSessionMetadata:
    def test_updates_metadata(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        req = UpdateSessionMetadataRequest(
            video_link="https://teams.microsoft.com/l/meetup-join/123",
            video_platform=VideoPlatform.TEAMS,
            duration_minutes=60,
            notes="Updated notes",
        )

        updated, _ = service.update_session_metadata(session.id, user_id, req)

        assert updated.video_link == "https://teams.microsoft.com/l/meetup-join/123"
        assert updated.video_platform == "teams"
        assert updated.duration_minutes == 60
        assert updated.notes == "Updated notes"

    def test_reschedule(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        req = UpdateSessionMetadataRequest(scheduled_at="2026-03-08T15:00:00Z")

        updated, _ = service.update_session_metadata(session.id, user_id, req)

        assert updated.scheduled_at == "2026-03-08T15:00:00Z"
        assert updated.session_date == "2026-03-08T15:00:00Z"

    def test_terminal_status_raises(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        # Move to cancelled
        session.status = SessionStatus.CANCELLED
        session_repo.update(session)

        req = UpdateSessionMetadataRequest(notes="Should fail")
        with pytest.raises(SessionInTerminalStatusError):
            service.update_session_metadata(session.id, user_id, req)

    def test_session_not_found(self, service: SessionService, user_id: str) -> None:
        req = UpdateSessionMetadataRequest(notes="x")
        with pytest.raises(SessionNotFoundError):
            service.update_session_metadata("nonexistent", user_id, req)

# --- Transcript upload tests ---

class TestUploadTranscriptToSession:
    def test_uploads_and_triggers_soap(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(
            session_repo, user_id, patient.id, SessionStatus.RECORDING_COMPLETE
        )
        req = UploadTranscriptToSessionRequest(
            format="google_meet",
            content="Therapist: Hello\nClient: Hi",
        )

        updated = service.upload_transcript_to_session(session.id, user_id, req)

        assert updated.status == SessionStatus.PENDING_REVIEW
        assert updated.transcript.content == "Therapist: Hello\nClient: Hi"
        assert updated.transcript.format == "google_meet"
        assert updated.soap_note is not None
        assert updated.processing_started_at is not None
        assert updated.processing_completed_at is not None

    def test_wrong_status_raises(
        self,
        service: SessionService,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        session = _make_session(session_repo, user_id, patient.id, SessionStatus.SCHEDULED)
        req = UploadTranscriptToSessionRequest(
            format="txt", content="content"
        )
        with pytest.raises(InvalidSessionStatusError):
            service.upload_transcript_to_session(session.id, user_id, req)

    def test_session_not_found(self, service: SessionService, user_id: str) -> None:
        req = UploadTranscriptToSessionRequest(format="txt", content="content")
        with pytest.raises(SessionNotFoundError):
            service.upload_transcript_to_session("nonexistent", user_id, req)

# --- Today's sessions repository test ---

class TestListTodaySessions:
    def test_returns_today_only(
        self,
        session_repo: InMemoryTherapySessionRepository,
        user_id: str,
        patient: Patient,
    ) -> None:
        now = datetime.now(UTC)
        today_iso = now.isoformat().replace("+00:00", "Z")
        yesterday_iso = (now - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        tomorrow_iso = (now + timedelta(days=1)).isoformat().replace("+00:00", "Z")

        _make_session(session_repo, user_id, patient.id, scheduled_at=today_iso)
        _make_session(session_repo, user_id, patient.id, scheduled_at=yesterday_iso)
        _make_session(session_repo, user_id, patient.id, scheduled_at=tomorrow_iso)

        today_sessions = session_repo.list_today_by_user(user_id, "UTC")

        assert len(today_sessions) == 1
        assert today_sessions[0].scheduled_at == today_iso

    def test_respects_user_isolation(
        self,
        session_repo: InMemoryTherapySessionRepository,
        patient: Patient,
    ) -> None:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        _make_session(session_repo, "user-a", patient.id, scheduled_at=now)
        _make_session(session_repo, "user-b", patient.id, scheduled_at=now)

        assert len(session_repo.list_today_by_user("user-a", "UTC")) == 1
        assert len(session_repo.list_today_by_user("user-b", "UTC")) == 1
        assert len(session_repo.list_today_by_user("user-c", "UTC")) == 0

# --- User preferences tests ---

class TestUserPreferences:
    def test_defaults(self) -> None:
        repo = InMemoryUserRepository()
        prefs = repo.get_preferences("unknown-user")

        assert prefs.default_video_platform == "zoom"
        assert prefs.default_session_type == "individual"
        assert prefs.default_duration_minutes == 50
        assert prefs.auto_transcribe is True
        assert prefs.quality_preset == "balanced"

    def test_save_and_retrieve(self) -> None:
        repo = InMemoryUserRepository()
        prefs = UserPreferences(
            default_video_platform="teams",
            default_duration_minutes=60,
            therapist_display_name="Dr. Smith",
        )
        repo.save_preferences("user1", prefs)

        retrieved = repo.get_preferences("user1")
        assert retrieved.default_video_platform == "teams"
        assert retrieved.default_duration_minutes == 60
        assert retrieved.therapist_display_name == "Dr. Smith"
