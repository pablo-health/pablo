# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for SchedulingService using InMemory repositories."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from app.scheduling_engine.exceptions import (
    AppointmentConflictError,
    AppointmentNotFoundError,
    InvalidAppointmentError,
)
from app.scheduling_engine.models.appointment import AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService

USER_ID = "user-1"
PATIENT_ID = "patient-1"


def _appt_data(**overrides: Any) -> dict[str, Any]:
    """Build appointment data dict with sensible defaults."""
    defaults: dict[str, Any] = {
        "patient_id": PATIENT_ID,
        "title": "Session",
        "start_at": datetime.fromisoformat("2026-03-20T14:00:00+00:00"),
        "end_at": datetime.fromisoformat("2026-03-20T14:50:00+00:00"),
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

    def test_rejects_overlapping_appointment(self, service: SchedulingService) -> None:
        service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(AppointmentConflictError):
            service.create_appointment(USER_ID, data=_appt_data(patient_id="patient-2"))

    def test_accepts_back_to_back_appointment(self, service: SchedulingService) -> None:
        service.create_appointment(USER_ID, data=_appt_data())
        second = service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-2",
                start_at=datetime.fromisoformat("2026-03-20T14:50:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T15:40:00+00:00"),
            ),
        )
        assert second.id

    def test_rebooks_slot_freed_by_cancellation(
        self, service: SchedulingService, repo: InMemoryAppointmentRepository
    ) -> None:
        first = service.create_appointment(USER_ID, data=_appt_data())
        service.cancel_appointment(first.id, USER_ID)
        second = service.create_appointment(USER_ID, data=_appt_data(patient_id="patient-2"))
        assert second.id

    def test_overlap_check_is_scoped_to_user(self, service: SchedulingService) -> None:
        service.create_appointment("other-user", data=_appt_data())
        appt = service.create_appointment(USER_ID, data=_appt_data())
        assert appt.id


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
                title="Monday",
                start_at=datetime.fromisoformat("2026-03-16T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-16T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Wednesday",
                start_at=datetime.fromisoformat("2026-03-18T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                title="Next Monday",
                start_at=datetime.fromisoformat("2026-03-23T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-23T14:50:00+00:00"),
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
                title="Mine",
                start_at=datetime.fromisoformat("2026-03-18T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            "other-user",
            data=_appt_data(
                title="Theirs",
                start_at=datetime.fromisoformat("2026-03-18T15:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T15:50:00+00:00"),
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

    def test_rejects_move_onto_another_appointment(self, service: SchedulingService) -> None:
        service.create_appointment(USER_ID, data=_appt_data())
        second = service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-2",
                start_at=datetime.fromisoformat("2026-03-20T16:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T16:50:00+00:00"),
            ),
        )
        with pytest.raises(AppointmentConflictError):
            service.update_appointment(
                second.id,
                USER_ID,
                start_at=datetime.fromisoformat("2026-03-20T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-20T14:50:00+00:00"),
            )

    def test_allows_move_that_only_overlaps_itself(self, service: SchedulingService) -> None:
        created = service.create_appointment(USER_ID, data=_appt_data())
        moved = service.update_appointment(
            created.id,
            USER_ID,
            start_at=datetime.fromisoformat("2026-03-20T14:10:00+00:00"),
            end_at=datetime.fromisoformat("2026-03-20T15:00:00+00:00"),
        )
        assert moved.start_at == datetime.fromisoformat("2026-03-20T14:10:00+00:00")

    def test_non_time_update_does_not_check_overlap(self, service: SchedulingService) -> None:
        """Mirrors the internal start-session call, which only ever attaches
        session_id: no time field is changing, so no collision check runs."""
        created = service.create_appointment(USER_ID, data=_appt_data())
        updated = service.update_appointment(created.id, USER_ID, session_id="session-1")
        assert updated.session_id == "session-1"


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
                start_at=datetime.fromisoformat("2026-03-18T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T14:50:00+00:00"),
            ),
        )
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-b",
                title="Session B",
                start_at=datetime.fromisoformat("2026-03-18T15:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-18T15:50:00+00:00"),
            ),
        )
        results = service.list_patient_appointments(USER_ID, "patient-a")
        assert len(results) == 1
        assert results[0].patient_id == "patient-a"


class TestCreateRecurring:
    def test_rejects_series_with_colliding_occurrence(
        self, service: SchedulingService, repo: InMemoryAppointmentRepository
    ) -> None:
        """A single occurrence colliding with an existing appointment fails
        the whole series — nothing from it is persisted."""
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                patient_id="patient-2",
                start_at=datetime.fromisoformat("2026-03-27T14:00:00+00:00"),
                end_at=datetime.fromisoformat("2026-03-27T14:50:00+00:00"),
            ),
        )

        with pytest.raises(AppointmentConflictError):
            service.create_recurring(
                USER_ID,
                data=_appt_data(),
                recurrence={"frequency": "weekly", "timezone": "UTC", "count": 4},
            )

        remaining = repo.list_by_range(USER_ID, "2026-03-19T00:00:00Z", "2026-04-11T00:00:00Z")
        assert len(remaining) == 1, "only the pre-existing blocker should remain"

    def test_creates_series_with_no_collisions(self, service: SchedulingService) -> None:
        appointments = service.create_recurring(
            USER_ID,
            data=_appt_data(),
            recurrence={"frequency": "weekly", "timezone": "UTC", "count": 4},
        )
        assert len(appointments) == 4
