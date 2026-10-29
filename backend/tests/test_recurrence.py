# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for RecurrenceGenerator and recurring appointment operations."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from app.scheduling_engine.exceptions import InvalidAppointmentError, InvalidRecurrenceError
from app.scheduling_engine.models.appointment import (
    Appointment,
    AppointmentStatus,
    RecurrenceFrequency,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.recurrence import RecurrenceGenerator
from app.scheduling_engine.services.scheduling import SchedulingService

USER_ID = "user-1"
PATIENT_ID = "patient-1"


def _appt_data(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "patient_id": PATIENT_ID,
        "title": "Weekly Session",
        "start_at": datetime.fromisoformat("2026-03-17T14:00:00+00:00"),
        "end_at": datetime.fromisoformat("2026-03-17T14:50:00+00:00"),
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


def _series_of_five_with_a_moved_fourth(
    service: SchedulingService, repo: InMemoryAppointmentRepository
) -> tuple[list[Appointment], list[dict[str, Any]]]:
    """A weekly series of five, occurrence 3 moved an hour later on its own.

    Moving one occurrence through ``update_appointment`` is what makes it an
    exception, so this is the shape every "this and following" case needs:
    two occurrences before the anchor, the anchor, an exception after it, and
    a plain occurrence last. Returns the series in order plus a snapshot of
    the first two, taken before the edit under test.
    """
    series = service.create_recurring(
        USER_ID, data=_appt_data(), recurrence=_recurrence(timezone="UTC", count=5)
    )
    third = series[3]
    service.update_appointment(
        third.id,
        USER_ID,
        start_at=third.start_at + timedelta(hours=1),
        end_at=third.end_at + timedelta(hours=1),
    )
    series = _series(repo, series[0])
    assert [a.is_exception for a in series] == [False, False, False, True, False]
    return series, [a.to_dict() for a in series[:2]]


def _series(repo: InMemoryAppointmentRepository, member: Appointment) -> list[Appointment]:
    """Every occurrence of ``member``'s series, in start order."""
    assert member.recurring_appointment_id is not None
    return repo.list_by_recurring_id(USER_ID, member.recurring_appointment_id)


class TestEditFutureOccurrences:
    def test_edits_the_anchor_and_everything_after_it(
        self, service: SchedulingService, repo: InMemoryAppointmentRepository
    ) -> None:
        series, before = _series_of_five_with_a_moved_fourth(service, repo)
        anchor, exception = series[2], series[3]

        updated = service.edit_future_occurrences(anchor.id, USER_ID, title="Updated Weekly")

        # Exactly the anchor and the two after it, in series order.
        assert [a.id for a in updated] == [a.id for a in series[2:]]
        assert all(a.title == "Updated Weekly" for a in updated)

        after = _series(repo, anchor)
        # The two before the anchor are untouched, field for field.
        assert [a.to_dict() for a in after[:2]] == before
        assert [a.title for a in after] == ["Weekly Session"] * 2 + ["Updated Weekly"] * 3

        # The exception is edited like any other following occurrence: it
        # takes the new title, keeps the time it was moved to, and stays an
        # exception. Being edited on its own does not take it out of the
        # series' future.
        edited_exception = after[3]
        assert edited_exception.id == exception.id
        assert edited_exception.start_at == exception.start_at
        assert edited_exception.is_exception is True
        assert [a.is_exception for a in after] == [False, False, False, True, False]

    def test_non_recurring_raises(self, service: SchedulingService) -> None:
        appt = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(InvalidAppointmentError, match="not part of a recurring"):
            service.edit_future_occurrences(appt.id, USER_ID, title="Nope")


class TestCancelFutureOccurrences:
    def test_cancels_the_anchor_and_everything_after_it(
        self, service: SchedulingService, repo: InMemoryAppointmentRepository
    ) -> None:
        series, before = _series_of_five_with_a_moved_fourth(service, repo)
        anchor, exception = series[2], series[3]

        cancelled = service.cancel_future_occurrences(anchor.id, USER_ID)

        assert [a.id for a in cancelled] == [a.id for a in series[2:]]
        assert all(a.status == AppointmentStatus.CANCELLED for a in cancelled)

        after = _series(repo, anchor)
        assert [a.to_dict() for a in after[:2]] == before
        assert [a.status for a in after] == [AppointmentStatus.CONFIRMED] * 2 + [
            AppointmentStatus.CANCELLED
        ] * 3

        # The moved exception is cancelled with the rest and keeps its mark.
        assert after[3].id == exception.id
        assert after[3].is_exception is True

    def test_non_recurring_raises(self, service: SchedulingService) -> None:
        appt = service.create_appointment(USER_ID, data=_appt_data())
        with pytest.raises(InvalidAppointmentError, match="not part of a recurring"):
            service.cancel_future_occurrences(appt.id, USER_ID)
