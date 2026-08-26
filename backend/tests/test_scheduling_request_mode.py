# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request mode: a booking that has been asked for but not yet agreed to.

The load-bearing property, and the reason the expiry is mandatory rather than
optional: a pending request OCCUPIES ITS SLOT. That is what stops the same hour
being offered to two people while one of them is being decided about — and it is
also what would let a request queue nobody reads quietly eat a therapist's
calendar, if such a request could be created without an expiry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.scheduling_engine.exceptions import InvalidAppointmentError
from app.scheduling_engine.models.appointment import AppointmentStatus
from app.scheduling_engine.models.availability import AvailabilityRule
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService

USER_ID = "user-1"
PATIENT_ID = "patient-1"
DAY = "2026-03-20"  # a Friday
FAR_FUTURE = datetime.fromisoformat("2099-01-01T00:00:00+00:00")


def _appt_data(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "patient_id": PATIENT_ID,
        "title": "Session",
        "start_at": datetime.fromisoformat(f"{DAY}T14:00:00+00:00"),
        "end_at": datetime.fromisoformat(f"{DAY}T14:50:00+00:00"),
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


class TestCreatingARequest:
    def test_a_create_without_a_status_is_confirmed_exactly_as_before(
        self, service: SchedulingService
    ) -> None:
        # Every existing caller passes no status, and must be unaffected.
        appt = service.create_appointment(USER_ID, data=_appt_data())
        assert appt.status == AppointmentStatus.CONFIRMED
        assert appt.pending_expires_at is None

    def test_a_request_is_created_pending_with_its_expiry(
        self, service: SchedulingService
    ) -> None:
        appt = service.create_appointment(
            USER_ID,
            data=_appt_data(status="pending", pending_expires_at=FAR_FUTURE),
        )
        assert appt.status == AppointmentStatus.PENDING
        assert appt.pending_expires_at == FAR_FUTURE

    def test_a_pending_request_without_an_expiry_is_refused(
        self, service: SchedulingService
    ) -> None:
        # The footgun cannot be loaded: a pending appointment holds a slot, so
        # one with no expiry holds it for ever.
        with pytest.raises(InvalidAppointmentError, match="must carry pending_expires_at"):
            service.create_appointment(USER_ID, data=_appt_data(status="pending"))

    def test_an_expiry_on_a_confirmed_booking_is_refused(
        self, service: SchedulingService
    ) -> None:
        # It would arm the sweep against a real appointment.
        with pytest.raises(InvalidAppointmentError, match="only meaningful"):
            service.create_appointment(
                USER_ID, data=_appt_data(pending_expires_at=FAR_FUTURE)
            )

    @pytest.mark.parametrize("status", ["cancelled", "completed", "no_show"])
    def test_an_appointment_cannot_be_born_in_a_terminal_state(
        self, service: SchedulingService, status: str
    ) -> None:
        with pytest.raises(InvalidAppointmentError, match="Cannot create"):
            service.create_appointment(USER_ID, data=_appt_data(status=status))

    def test_an_unknown_status_is_refused_rather_than_stored(
        self, service: SchedulingService
    ) -> None:
        with pytest.raises(InvalidAppointmentError, match="Unknown appointment status"):
            service.create_appointment(USER_ID, data=_appt_data(status="probably"))

    def test_an_expiry_may_arrive_as_an_iso_string(self, service: SchedulingService) -> None:
        appt = service.create_appointment(
            USER_ID,
            data=_appt_data(status="pending", pending_expires_at="2099-01-01T00:00:00Z"),
        )
        assert appt.pending_expires_at == FAR_FUTURE


class TestConfirming:
    def test_confirming_a_request_clears_the_expiry(
        self, repo: InMemoryAppointmentRepository, service: SchedulingService
    ) -> None:
        repo.grant_access(PATIENT_ID, USER_ID)
        appt = service.create_appointment(
            USER_ID, data=_appt_data(status="pending", pending_expires_at=FAR_FUTURE)
        )

        confirmed = service.confirm_appointment(appt.id, USER_ID)

        assert confirmed.status == AppointmentStatus.CONFIRMED
        # Left behind, it would arm the sweep against a now-real appointment.
        assert confirmed.pending_expires_at is None

    def test_only_a_pending_appointment_can_be_confirmed(
        self, repo: InMemoryAppointmentRepository, service: SchedulingService
    ) -> None:
        repo.grant_access(PATIENT_ID, USER_ID)
        appt = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(InvalidAppointmentError, match="Only a pending appointment"):
            service.confirm_appointment(appt.id, USER_ID)


class TestExpiring:
    def _pending(self, service: SchedulingService, expires_at: datetime) -> Any:
        return service.create_appointment(
            USER_ID, data=_appt_data(status="pending", pending_expires_at=expires_at)
        )

    def test_a_request_nobody_answered_stops_holding_its_slot(
        self, repo: InMemoryAppointmentRepository, service: SchedulingService
    ) -> None:
        repo.grant_access(PATIENT_ID, USER_ID)
        self._pending(service, datetime.now(UTC) - timedelta(hours=1))

        expired = service.expire_pending_appointments(USER_ID)

        assert len(expired) == 1
        # Cancelled, not deleted: a patient who rings back to ask "did you get
        # my request?" needs somebody to be able to see that it lapsed.
        assert expired[0].status == AppointmentStatus.CANCELLED
        assert expired[0].pending_expires_at is None

    def test_a_request_still_in_time_is_left_alone(
        self, repo: InMemoryAppointmentRepository, service: SchedulingService
    ) -> None:
        repo.grant_access(PATIENT_ID, USER_ID)
        self._pending(service, datetime.now(UTC) + timedelta(hours=1))
        assert service.expire_pending_appointments(USER_ID) == []

    def test_the_sweep_does_not_reach_another_practice(
        self, repo: InMemoryAppointmentRepository, service: SchedulingService
    ) -> None:
        repo.grant_access(PATIENT_ID, USER_ID)
        self._pending(service, datetime.now(UTC) - timedelta(hours=1))
        assert service.expire_pending_appointments("someone-else") == []


class TestPendingHoldsTheSlot:
    """The property the whole expiry contract exists because of."""

    def _engine(self, appt_repo: InMemoryAppointmentRepository) -> AvailabilityEngine:
        rules = InMemoryAvailabilityRuleRepository()
        rules.create(
            AvailabilityRule(
                id="rule-1",
                user_id=USER_ID,
                rule_type="working_hours",
                # 2026-03-20 is a Friday; Monday is 0.
                params={"day_of_week": 4, "start": "14:00", "end": "15:00"},
                enforcement="hard",
            )
        )
        return AvailabilityEngine(rules, appt_repo)

    def test_a_pending_request_is_not_offered_to_somebody_else(
        self, repo: InMemoryAppointmentRepository, service: SchedulingService
    ) -> None:
        engine = self._engine(repo)
        before = engine.get_free_slots(USER_ID, DAY, 50)
        assert any(s.start.startswith(f"{DAY}T14:00") for s in before.slots)

        service.create_appointment(
            USER_ID, data=_appt_data(status="pending", pending_expires_at=FAR_FUTURE)
        )

        after = engine.get_free_slots(USER_ID, DAY, 50)
        assert not any(s.start.startswith(f"{DAY}T14:00") for s in after.slots)

    def test_expiring_a_request_gives_the_slot_back(
        self, repo: InMemoryAppointmentRepository, service: SchedulingService
    ) -> None:
        repo.grant_access(PATIENT_ID, USER_ID)
        engine = self._engine(repo)
        service.create_appointment(
            USER_ID,
            data=_appt_data(
                status="pending", pending_expires_at=datetime.now(UTC) - timedelta(hours=1)
            ),
        )
        assert not any(
            s.start.startswith(f"{DAY}T14:00") for s in engine.get_free_slots(USER_ID, DAY, 50).slots
        )

        service.expire_pending_appointments(USER_ID)

        assert any(
            s.start.startswith(f"{DAY}T14:00") for s in engine.get_free_slots(USER_ID, DAY, 50).slots
        )
