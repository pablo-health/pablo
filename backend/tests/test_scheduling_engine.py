# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for SchedulingService using InMemory repositories."""

from __future__ import annotations

import pytest
from app.scheduling_engine.exceptions import AppointmentNotFoundError, InvalidAppointmentError
from app.scheduling_engine.models.appointment import AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService

USER_ID = "user-1"
PATIENT_ID = "patient-1"


def _appt_data(**overrides: str | int | None) -> dict[str, str | int | None]:
    """Build appointment data dict with sensible defaults."""
    defaults: dict[str, str | int | None] = {
        "patient_id": PATIENT_ID,
        "title": "Session",
        "start_at": "2026-03-20T14:00:00Z",
        "end_at": "2026-03-20T14:50:00Z",
        "duration_minutes": 50,
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture
def repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def service(repo: InMemoryAppointmentRepository) -> SchedulingService:
    return SchedulingService(repo)


class TestCreateAppointment:
    def test_creates_appointment(self, service: SchedulingService) -> None:
        appt = service.create_appointment(USER_ID, data=_appt_data(title="Session with Patient"))
        assert appt.id
        assert appt.user_id == USER_ID
        assert appt.patient_id == PATIENT_ID
        assert appt.status == AppointmentStatus.CONFIRMED
        assert appt.session_type == "individual"
        assert appt.created_at

    def test_creates_with_optional_fields(self, service: SchedulingService) -> None:
        appt = service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Couples Session",
                session_type="couples",
                video_link="https://zoom.us/j/123",
                video_platform="zoom",
                notes="First session",
            ),
        )
        assert appt.session_type == "couples"
        assert appt.video_link == "https://zoom.us/j/123"
        assert appt.video_platform == "zoom"
        assert appt.notes == "First session"

    def test_rejects_empty_patient_id(self, service: SchedulingService) -> None:
        with pytest.raises(InvalidAppointmentError, match="patient_id"):
            service.create_appointment(USER_ID, data=_appt_data(patient_id=""))

    def test_rejects_invalid_duration(self, service: SchedulingService) -> None:
        with pytest.raises(InvalidAppointmentError, match="duration_minutes"):
            service.create_appointment(USER_ID, data=_appt_data(duration_minutes=0))


class TestGetAppointment:
    def test_gets_existing_appointment(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        fetched = service.get_appointment(created.id, USER_ID)
        assert fetched.id == created.id

    def test_raises_for_missing_appointment(self, service: SchedulingService) -> None:
        with pytest.raises(AppointmentNotFoundError):
            service.get_appointment("nonexistent", USER_ID)

    def test_raises_for_wrong_user(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(AppointmentNotFoundError):
            service.get_appointment(created.id, "other-user")


class TestListAppointments:
    def test_lists_in_range(self, service: SchedulingService) -> None:
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Monday", start_at="2026-03-16T14:00:00Z", end_at="2026-03-16T14:50:00Z"
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Wednesday", start_at="2026-03-18T14:00:00Z", end_at="2026-03-18T14:50:00Z"
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Next Monday",
                start_at="2026-03-23T14:00:00Z",
                end_at="2026-03-23T14:50:00Z",
            ),
        )
        results = service.list_appointments(USER_ID, "2026-03-16T00:00:00Z", "2026-03-20T00:00:00Z")
        assert len(results) == 2
        assert results[0].title == "Monday"
        assert results[1].title == "Wednesday"

    def test_excludes_other_users(self, service: SchedulingService) -> None:
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Mine", start_at="2026-03-18T14:00:00Z", end_at="2026-03-18T14:50:00Z"
            ),
        )
        service.create_appointment(
            "other-user",
            data=_appt_data(
                title="Theirs", start_at="2026-03-18T15:00:00Z", end_at="2026-03-18T15:50:00Z"
            ),
        )
        results = service.list_appointments(USER_ID, "2026-03-18T00:00:00Z", "2026-03-19T00:00:00Z")
        assert len(results) == 1
        assert results[0].title == "Mine"


class TestUpdateAppointment:
    def test_updates_fields(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data(title="Original"))
        updated = service.update_appointment(
            created.id, USER_ID, title="Updated", notes="Added notes"
        )
        assert updated.title == "Updated"
        assert updated.notes == "Added notes"
        assert updated.updated_at is not None

    def test_rejects_disallowed_fields(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(InvalidAppointmentError, match="Cannot update field"):
            service.update_appointment(created.id, USER_ID, id="new-id")

    def test_marks_recurring_as_exception(
        self,
        repo: InMemoryAppointmentRepository,
        service: SchedulingService,
    ) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data(title="Weekly"))
        created.recurring_appointment_id = "master-id"
        repo.update(created)

        updated = service.update_appointment(created.id, USER_ID, title="Moved")
        assert updated.is_exception is True


class TestCancelAppointment:
    def test_cancels_appointment(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        cancelled = service.cancel_appointment(created.id, USER_ID)
        assert cancelled.status == AppointmentStatus.CANCELLED

    def test_cancel_nonexistent_raises(self, service: SchedulingService) -> None:
        with pytest.raises(AppointmentNotFoundError):
            service.cancel_appointment("nonexistent", USER_ID)


class TestListPatientAppointments:
    def test_lists_by_patient(self, service: SchedulingService) -> None:
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-a",
                title="Session A",
                start_at="2026-03-18T14:00:00Z",
                end_at="2026-03-18T14:50:00Z",
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-b",
                title="Session B",
                start_at="2026-03-18T15:00:00Z",
                end_at="2026-03-18T15:50:00Z",
            ),
        )
        results = service.list_patient_appointments(USER_ID, "patient-a")
        assert len(results) == 1
        assert results[0].patient_id == "patient-a"
