# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The open-slot event: which cancellations raise it, and what it carries.

Only a client's own outright cancellation opens a slot. Each other way an
appointment ends up cancelled is driven through the real engine call that
produces it — not a hand-built row — so the test fails if one of those paths
ever starts recording the client as the canceller.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.scheduling_engine.models.appointment import (
    Appointment,
    CancellationActor,
    ChangeRecord,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services import open_slots
from app.scheduling_engine.services.open_slots import OpenSlot, open_slot_for, subscribe
from app.scheduling_engine.services.scheduling import SchedulingService

if TYPE_CHECKING:
    from collections.abc import Iterator

CLINICIAN = "clinician-1"
CLIENT = "patient-1"
TYPE_ID = "type-50"
START = datetime(2099, 3, 20, 14, 0, tzinfo=UTC)
END = START + timedelta(minutes=50)


def _data(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "patient_id": CLIENT,
        "title": "Therapy session",
        "start_at": START,
        "end_at": END,
        "duration_minutes": 50,
        "session_type": "Therapy session",
        "appointment_type_id": TYPE_ID,
    }
    data.update(overrides)
    return data


@pytest.fixture
def service() -> SchedulingService:
    return SchedulingService(InMemoryAppointmentRepository())


@pytest.fixture
def heard() -> Iterator[list[OpenSlot]]:
    received: list[OpenSlot] = []
    unsubscribe = subscribe(received.append)
    yield received
    unsubscribe()


def _client_cancels(service: SchedulingService, appt: Appointment) -> Appointment:
    return service.cancel_appointment(
        appt.id, CLINICIAN, record=ChangeRecord(by=CancellationActor.PATIENT, by_id=CLIENT)
    )


class TestWhichCancellationsOpenASlot:
    def test_a_client_cancellation_opens_the_slot_with_its_type_and_times(
        self, service: SchedulingService
    ) -> None:
        appt = service.create_appointment(CLINICIAN, data=_data())

        slot = open_slot_for(_client_cancels(service, appt))

        assert slot == OpenSlot(
            appointment_id=appt.id,
            clinician_id=CLINICIAN,
            start_at=START,
            end_at=END,
            duration_minutes=50,
            appointment_type_id=TYPE_ID,
            session_type="Therapy session",
            modality="in_person",
        )

    def test_a_clinician_cancellation_opens_nothing(self, service: SchedulingService) -> None:
        appt = service.create_appointment(CLINICIAN, data=_data())

        # The in-app cancel: no record means the clinician did it.
        assert open_slot_for(service.cancel_appointment(appt.id, CLINICIAN)) is None

    def test_a_lapsed_hold_opens_nothing(self, service: SchedulingService) -> None:
        service.create_appointment(
            CLINICIAN,
            data=_data(status="pending", pending_expires_at=datetime(2000, 1, 1, tzinfo=UTC)),
        )

        [lapsed] = service.expire_pending_appointments(CLINICIAN)

        assert lapsed.cancelled_by == CancellationActor.SYSTEM
        assert open_slot_for(lapsed) is None

    def test_a_system_cancellation_opens_nothing(self, service: SchedulingService) -> None:
        appt = service.create_appointment(CLINICIAN, data=_data())

        # What a calendar sync records when the event it followed went away.
        cancelled = service.cancel_appointment(
            appt.id, CLINICIAN, record=ChangeRecord(by=CancellationActor.SYSTEM, late=False)
        )

        assert open_slot_for(cancelled) is None

    def test_a_client_moving_their_appointment_opens_nothing(
        self, service: SchedulingService
    ) -> None:
        appt = service.create_appointment(CLINICIAN, data=_data())

        service.reschedule_appointment(
            appt.id,
            CLINICIAN,
            start_at=START + timedelta(minutes=30),
            record=ChangeRecord(by=CancellationActor.PATIENT, by_id=CLIENT),
        )

        given_up = service.get_appointment(appt.id, CLINICIAN)
        assert given_up.cancelled_by == CancellationActor.PATIENT
        assert open_slot_for(given_up) is None

    def test_an_appointment_still_booked_opens_nothing(self, service: SchedulingService) -> None:
        assert open_slot_for(service.create_appointment(CLINICIAN, data=_data())) is None


class TestModality:
    def test_a_meeting_link_makes_it_telehealth(self, service: SchedulingService) -> None:
        appt = service.create_appointment(
            CLINICIAN, data=_data(video_link="https://meet.example.test/abc")
        )
        slot = open_slot_for(_client_cancels(service, appt))
        assert slot is not None
        assert slot.modality == "telehealth"

    def test_a_meeting_provider_makes_it_telehealth(self, service: SchedulingService) -> None:
        appt = service.create_appointment(CLINICIAN, data=_data())
        service.update_appointment(appt.id, CLINICIAN, provider="google_meet")
        slot = open_slot_for(_client_cancels(service, appt))
        assert slot is not None
        assert slot.modality == "telehealth"


class TestSubscribing:
    def _slot(self) -> OpenSlot:
        return OpenSlot(
            appointment_id="appt-1",
            clinician_id=CLINICIAN,
            start_at=START,
            end_at=END,
            duration_minutes=50,
            appointment_type_id=TYPE_ID,
            session_type="Therapy session",
            modality="in_person",
        )

    def test_a_subscriber_hears_what_is_published(self, heard: list[OpenSlot]) -> None:
        open_slots.publish(self._slot())
        assert heard == [self._slot()]

    def test_an_unsubscribed_handler_hears_nothing_more(self) -> None:
        received: list[OpenSlot] = []
        unsubscribe = subscribe(received.append)
        unsubscribe()

        open_slots.publish(self._slot())

        assert received == []

    def test_a_failing_subscriber_does_not_stop_the_next(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def broken(_: OpenSlot) -> None:
            raise RuntimeError("subscriber down")

        received: list[OpenSlot] = []
        unsubscribers = [subscribe(broken), subscribe(received.append)]
        try:
            open_slots.publish(self._slot())
        finally:
            for unsubscribe in unsubscribers:
                unsubscribe()

        assert received == [self._slot()]
        assert "handler=" in caplog.text and "broken" in caplog.text
        # Nothing from the slot itself reaches the log.
        assert "appt-1" not in caplog.text
