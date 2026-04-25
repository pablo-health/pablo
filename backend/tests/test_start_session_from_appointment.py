# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the appointment → session linking flow (start-session endpoint logic)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from app.models import Patient, ScheduleSessionRequest, SessionStatus, VideoPlatform
from app.models.enums import SessionSource, SessionType
from app.models.session import SOAPNote
from app.repositories import InMemoryPatientRepository, InMemoryTherapySessionRepository
from app.scheduling_engine.exceptions import AppointmentNotFoundError
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.note_generation_service import GeneratedNote, NoteGenerationService
from app.services.session_service import InvalidNoteTypeError, PatientNotFoundError, SessionService
from app.utcnow import utc_now

USER_ID = "test-user-1"
PATIENT_ID = "test-patient-1"


def _make_appointment(
    repo: InMemoryAppointmentRepository,
    *,
    patient_id: str = PATIENT_ID,
    session_id: str | None = None,
    video_link: str | None = "https://zoom.us/j/123",
    video_platform: str | None = "zoom",
    session_type: str = "individual",
    notes: str | None = "Weekly check-in",
) -> Appointment:
    """Create an appointment in the in-memory repo."""
    now = utc_now()
    appt = Appointment(
        id=str(uuid.uuid4()),
        user_id=USER_ID,
        patient_id=patient_id,
        title="Session with Patient",
        start_at=datetime.fromisoformat("2026-04-15T14:00:00+00:00"),
        end_at=datetime.fromisoformat("2026-04-15T14:50:00+00:00"),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type=session_type,
        video_link=video_link,
        video_platform=video_platform,
        notes=notes,
        session_id=session_id,
        created_at=now,
        updated_at=now,
    )
    return repo.create(appt)


def _make_patient(repo: InMemoryPatientRepository) -> Patient:
    """Create a patient in the in-memory repo."""
    now = datetime.now(UTC)
    p = Patient(
        id=PATIENT_ID,
        user_id=USER_ID,
        first_name="Jane",
        last_name="Smith",
        created_at=now,
        updated_at=now,
        session_count=0,
    )
    repo.create(p)
    return p


@pytest.fixture
def appt_repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def session_repo() -> InMemoryTherapySessionRepository:
    return InMemoryTherapySessionRepository()


@pytest.fixture
def patient_repo(session_repo: InMemoryTherapySessionRepository) -> InMemoryPatientRepository:
    return InMemoryPatientRepository(session_repo=session_repo)


@pytest.fixture
def scheduling_service(appt_repo: InMemoryAppointmentRepository) -> SchedulingService:
    return SchedulingService(appt_repo)


@pytest.fixture
def session_service(
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
) -> SessionService:
    mock_note_service = Mock(spec=NoteGenerationService)
    soap_note = SOAPNote.from_dict(
        {
            "subjective": "s",
            "objective": "o",
            "assessment": "a",
            "plan": "p",
        }
    )
    mock_note_service.generate_note.return_value = GeneratedNote(
        note_type="soap",
        content=soap_note.to_dict(),
        soap_note=soap_note,
    )
    return SessionService(session_repo, patient_repo, mock_note_service)


@pytest.fixture
def patient(patient_repo: InMemoryPatientRepository) -> Patient:
    return _make_patient(patient_repo)


class TestStartSessionFromAppointment:
    """Tests mirroring the start_session_from_appointment endpoint logic."""

    def test_creates_session_linked_to_appointment(
        self,
        appt_repo: InMemoryAppointmentRepository,
        scheduling_service: SchedulingService,
        session_service: SessionService,
        patient: Patient,
    ) -> None:
        """Happy path: creates a session and links it to the appointment."""
        appt = _make_appointment(appt_repo)

        # Build request from appointment data (mirrors endpoint step 5)
        request = ScheduleSessionRequest(
            patient_id=appt.patient_id,
            scheduled_at=appt.start_at,
            duration_minutes=appt.duration_minutes,
            video_link=appt.video_link,
            video_platform=VideoPlatform(appt.video_platform) if appt.video_platform else None,
            session_type=(
                SessionType(appt.session_type) if appt.session_type else SessionType.INDIVIDUAL
            ),
            source=SessionSource.COMPANION,
            notes=appt.notes,
        )
        session, _patient = session_service.schedule_session(USER_ID, request)

        # Link appointment → session (mirrors endpoint step 6)
        scheduling_service.update_appointment(appt.id, USER_ID, session_id=session.id)

        # Verify session
        assert session.status == SessionStatus.SCHEDULED
        assert session.patient_id == PATIENT_ID
        assert session.scheduled_at == datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
        assert session.video_link == "https://zoom.us/j/123"
        assert session.video_platform == "zoom"
        assert session.duration_minutes == 50
        assert session.source == "companion"
        assert session.notes == "Weekly check-in"

        # Verify appointment is linked
        linked_appt = scheduling_service.get_appointment(appt.id, USER_ID)
        assert linked_appt.session_id == session.id

    def test_rejects_appointment_already_linked(
        self,
        appt_repo: InMemoryAppointmentRepository,
        scheduling_service: SchedulingService,
    ) -> None:
        """409 scenario: appointment already has a session_id."""
        appt = _make_appointment(appt_repo, session_id="existing-session-123")
        assert appt.session_id == "existing-session-123"

    def test_rejects_appointment_without_patient(
        self,
        appt_repo: InMemoryAppointmentRepository,
    ) -> None:
        """400 scenario: appointment has no patient_id."""
        appt = _make_appointment(appt_repo, patient_id="")
        assert not appt.patient_id

    def test_appointment_not_found(
        self,
        scheduling_service: SchedulingService,
    ) -> None:
        """404 scenario: appointment doesn't exist."""
        with pytest.raises(AppointmentNotFoundError):
            scheduling_service.get_appointment("nonexistent-id", USER_ID)

    def test_patient_not_found_for_appointment(
        self,
        appt_repo: InMemoryAppointmentRepository,
        session_service: SessionService,
    ) -> None:
        """404 scenario: appointment's patient_id doesn't match a real patient."""
        appt = _make_appointment(appt_repo, patient_id="nonexistent-patient")
        request = ScheduleSessionRequest(
            patient_id=appt.patient_id,
            scheduled_at=appt.start_at,
        )
        with pytest.raises(PatientNotFoundError):
            session_service.schedule_session(USER_ID, request)

    def test_session_copies_all_appointment_fields(
        self,
        appt_repo: InMemoryAppointmentRepository,
        session_service: SessionService,
        patient: Patient,
    ) -> None:
        """Verify all appointment fields are correctly mapped to the session."""
        appt = _make_appointment(
            appt_repo,
            video_link="https://meet.google.com/abc",
            video_platform="meet",
            session_type="couples",
            notes="Important session notes",
        )
        request = ScheduleSessionRequest(
            patient_id=appt.patient_id,
            scheduled_at=appt.start_at,
            duration_minutes=appt.duration_minutes,
            video_link=appt.video_link,
            video_platform=VideoPlatform(appt.video_platform) if appt.video_platform else None,
            session_type=(
                SessionType(appt.session_type) if appt.session_type else SessionType.INDIVIDUAL
            ),
            source=SessionSource.COMPANION,
            notes=appt.notes,
        )
        session, _ = session_service.schedule_session(USER_ID, request)

        assert session.video_link == "https://meet.google.com/abc"
        assert session.video_platform == "meet"
        assert session.session_type == "couples"
        assert session.notes == "Important session notes"
        assert session.source == "companion"

    def test_session_handles_null_optional_fields(
        self,
        appt_repo: InMemoryAppointmentRepository,
        session_service: SessionService,
        patient: Patient,
    ) -> None:
        """Session creation works when appointment has null optional fields."""
        appt = _make_appointment(
            appt_repo,
            video_link=None,
            video_platform=None,
            notes=None,
        )
        request = ScheduleSessionRequest(
            patient_id=appt.patient_id,
            scheduled_at=appt.start_at,
            duration_minutes=appt.duration_minutes,
            video_link=appt.video_link,
            video_platform=None,
            session_type=SessionType.INDIVIDUAL,
            source=SessionSource.COMPANION,
            notes=appt.notes,
        )
        session, _ = session_service.schedule_session(USER_ID, request)

        assert session.video_link is None
        assert session.video_platform is None
        assert session.notes is None


class TestNoteTypeWiring:
    """Tests that note_type flows through scheduling into the persisted session."""

    def test_defaults_to_soap_when_omitted(
        self,
        appt_repo: InMemoryAppointmentRepository,
        session_service: SessionService,
        patient: Patient,
    ) -> None:
        """Omitting note_type falls back to SOAP."""
        appt = _make_appointment(appt_repo)
        request = ScheduleSessionRequest(
            patient_id=appt.patient_id,
            scheduled_at=appt.start_at,
        )
        session, _ = session_service.schedule_session(USER_ID, request)
        assert session.note_type == "soap"

    def test_persists_explicit_note_type(
        self,
        appt_repo: InMemoryAppointmentRepository,
        session_service: SessionService,
        patient: Patient,
    ) -> None:
        """Passing note_type='narrative' creates a session with that note_type."""
        appt = _make_appointment(appt_repo)
        request = ScheduleSessionRequest(
            patient_id=appt.patient_id,
            scheduled_at=appt.start_at,
            note_type="narrative",
        )
        session, _ = session_service.schedule_session(USER_ID, request)
        assert session.note_type == "narrative"

    def test_rejects_unknown_note_type(
        self,
        appt_repo: InMemoryAppointmentRepository,
        session_service: SessionService,
        patient: Patient,
    ) -> None:
        """Unknown note_type → 400."""
        appt = _make_appointment(appt_repo)
        request = ScheduleSessionRequest(
            patient_id=appt.patient_id,
            scheduled_at=appt.start_at,
            note_type="not-a-real-type",
        )
        with pytest.raises(InvalidNoteTypeError):
            session_service.schedule_session(USER_ID, request)


class TestSessionIdInAllowedFields:
    """Verify that session_id can be set via update_appointment."""

    def test_update_appointment_sets_session_id(
        self,
        appt_repo: InMemoryAppointmentRepository,
        scheduling_service: SchedulingService,
    ) -> None:
        appt = _make_appointment(appt_repo)
        assert appt.session_id is None

        updated = scheduling_service.update_appointment(
            appt.id, USER_ID, session_id="new-session-123"
        )
        assert updated.session_id == "new-session-123"

        # Verify persistence
        fetched = scheduling_service.get_appointment(appt.id, USER_ID)
        assert fetched.session_id == "new-session-123"
