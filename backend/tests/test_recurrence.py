# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for RecurrenceGenerator and recurring appointment operations."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from app.scheduling_engine.exceptions import InvalidAppointmentError, InvalidRecurrenceError
from app.scheduling_engine.models.appointment import AppointmentStatus, RecurrenceFrequency
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.recurrence import RecurrenceGenerator
from app.scheduling_engine.services.scheduling import SchedulingService

USER_ID = "user-1"
PATIENT_ID = "patient-1"


def _appt_data(**overrides: str | int | None) -> dict[str, str | int | None]:
    defaults: dict[str, str | int | None] = {
        "patient_id": PATIENT_ID,
        "title": "Weekly Session",
        "start_at": "2026-03-17T14:00:00Z",
        "end_at": "2026-03-17T14:50:00Z",
        "duration_minutes": 50,
    }
    defaults.update(overrides)
    return defaults


def _recurrence(
    frequency: str = "weekly",
    timezone: str = "America/New_York",
    **kw: str | int | None,
) -> dict[str, str | int | None]:
    result: dict[str, str | int | None] = {
        "frequency": frequency,
        "timezone": timezone,
    }
    result.update(kw)
    return result


@pytest.fixture
def repo() -> InMemoryAppointmentRepository:
    return InMemoryAppointmentRepository()


@pytest.fixture
def service(repo: InMemoryAppointmentRepository) -> SchedulingService:
    return SchedulingService(repo)


class TestRecurrenceGenerator:
    def test_weekly_generates_correct_count(self) -> None:
        start = datetime(2026, 3, 17, 14, 0, tzinfo=ZoneInfo("UTC"))
        occurrences = RecurrenceGenerator.generate(
            start, RecurrenceFrequency.WEEKLY, "America/New_York", count=4
        )
        assert len(occurrences) == 4
        for i in range(1, len(occurrences)):
            delta = occurrences[i] - occurrences[i - 1]
            assert delta.days == 7

    def test_biweekly_generates_14_day_intervals(self) -> None:
        start = datetime(2026, 3, 17, 14, 0, tzinfo=ZoneInfo("UTC"))
        occurrences = RecurrenceGenerator.generate(
            start, RecurrenceFrequency.BIWEEKLY, "America/New_York", count=3
        )
        assert len(occurrences) == 3
        for i in range(1, len(occurrences)):
            delta = occurrences[i] - occurrences[i - 1]
            assert delta.days == 14

    def test_monthly_generates_monthly_intervals(self) -> None:
        start = datetime(2026, 3, 17, 14, 0, tzinfo=ZoneInfo("UTC"))
        occurrences = RecurrenceGenerator.generate(
            start, RecurrenceFrequency.MONTHLY, "America/New_York", count=3
        )
        assert len(occurrences) == 3
        assert occurrences[0].month == 3
        assert occurrences[1].month == 4
        assert occurrences[2].month == 5

    def test_dst_spring_forward_preserves_local_time(self) -> None:
        """2pm ET stays at 2pm ET across DST spring-forward (Mar 8 2026)."""
        start_utc = datetime(2026, 3, 3, 19, 0, tzinfo=ZoneInfo("UTC"))  # 2pm ET (EST = UTC-5)
        occurrences = RecurrenceGenerator.generate(
            start_utc, RecurrenceFrequency.WEEKLY, "America/New_York", count=3
        )
        et = ZoneInfo("America/New_York")
        for occ in occurrences:
            local = occ.replace(tzinfo=ZoneInfo("UTC")).astimezone(et)
            assert local.hour == 14, f"Expected 2pm ET, got {local.hour}:00"

    def test_dst_fall_back_preserves_local_time(self) -> None:
        """2pm ET stays at 2pm ET across DST fall-back (Nov 1 2026)."""
        start_utc = datetime(2026, 10, 27, 18, 0, tzinfo=ZoneInfo("UTC"))  # 2pm ET (EDT = UTC-4)
        occurrences = RecurrenceGenerator.generate(
            start_utc, RecurrenceFrequency.WEEKLY, "America/New_York", count=3
        )
        et = ZoneInfo("America/New_York")
        for occ in occurrences:
            local = occ.replace(tzinfo=ZoneInfo("UTC")).astimezone(et)
            assert local.hour == 14, f"Expected 2pm ET, got {local.hour}:00"

    def test_default_horizon_6_months(self) -> None:
        start = datetime(2026, 1, 1, 14, 0, tzinfo=ZoneInfo("UTC"))
        occurrences = RecurrenceGenerator.generate(start, RecurrenceFrequency.WEEKLY, "UTC")
        assert 25 <= len(occurrences) <= 27

    def test_end_date_limits_generation(self) -> None:
        start = datetime(2026, 3, 17, 14, 0, tzinfo=ZoneInfo("UTC"))
        occurrences = RecurrenceGenerator.generate(
            start,
            RecurrenceFrequency.WEEKLY,
            "UTC",
            end_date=date(2026, 4, 7),
        )
        assert len(occurrences) == 4


class TestCreateRecurring:
    def test_creates_series(self, service: SchedulingService) -> None:
        appointments = service.create_recurring(
            USER_ID, data=_appt_data(), recurrence=_recurrence(count=4)
        )
        assert len(appointments) == 4
        master_id = appointments[0].recurring_appointment_id
        assert master_id is not None
        for i, appt in enumerate(appointments):
            assert appt.recurring_appointment_id == master_id
            assert appt.recurrence_index == i
            assert appt.recurrence_rule is not None

    def test_master_id_is_first_appointment_id(self, service: SchedulingService) -> None:
        appointments = service.create_recurring(
            USER_ID, data=_appt_data(), recurrence=_recurrence(timezone="UTC", count=3)
        )
        assert appointments[0].id == appointments[0].recurring_appointment_id

    def test_invalid_frequency_raises(self, service: SchedulingService) -> None:
        with pytest.raises(InvalidRecurrenceError, match="Invalid frequency"):
            service.create_recurring(
                USER_ID,
                data=_appt_data(),
                recurrence=_recurrence(frequency="daily", timezone="UTC", count=3),
            )

    def test_missing_patient_id_raises(self, service: SchedulingService) -> None:
        with pytest.raises(InvalidAppointmentError, match="patient_id"):
            service.create_recurring(
                USER_ID,
                data=_appt_data(patient_id=""),
                recurrence=_recurrence(timezone="UTC", count=3),
            )


class TestEditFutureOccurrences:
    def test_edits_future_occurrences(self, service: SchedulingService) -> None:
        appointments = service.create_recurring(
            USER_ID, data=_appt_data(), recurrence=_recurrence(timezone="UTC", count=4)
        )
        updated = service.edit_future_occurrences(
            appointments[1].id, USER_ID, title="Updated Weekly"
        )
        assert len(updated) >= 3
        for appt in updated:
            assert appt.title == "Updated Weekly"

    def test_non_recurring_raises(self, service: SchedulingService) -> None:
        appt = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(InvalidAppointmentError, match="not part of a recurring"):
            service.edit_future_occurrences(appt.id, USER_ID, title="Nope")


class TestCancelFutureOccurrences:
    def test_cancels_future_occurrences(self, service: SchedulingService) -> None:
        appointments = service.create_recurring(
            USER_ID, data=_appt_data(), recurrence=_recurrence(timezone="UTC", count=4)
        )
        cancelled = service.cancel_future_occurrences(appointments[1].id, USER_ID)
        assert len(cancelled) >= 3
        for appt in cancelled:
            assert appt.status == AppointmentStatus.CANCELLED

    def test_non_recurring_raises(self, service: SchedulingService) -> None:
        appt = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(InvalidAppointmentError, match="not part of a recurring"):
            service.cancel_future_occurrences(appt.id, USER_ID)
