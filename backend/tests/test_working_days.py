# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Working days and the due dates counted in them.

2026-10-12 is a Monday; the dates below are read against that.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from app.scheduling_engine.models.availability import AvailabilityRule, RuleType
from app.services.working_days import (
    MONDAY_TO_FRIDAY,
    WorkingCalendar,
    due_at,
    nth_working_day_after,
)

MON = date(2026, 10, 12)
TUE = date(2026, 10, 13)
WED = date(2026, 10, 14)
THU = date(2026, 10, 15)
FRI = date(2026, 10, 16)
SAT = date(2026, 10, 17)
NEXT_MON = date(2026, 10, 19)
NEXT_TUE = date(2026, 10, 20)

NEW_YORK = ZoneInfo("America/New_York")


def _rule(
    rule_type: RuleType, appointment_type_id: str | None = None, **params: object
) -> AvailabilityRule:
    return AvailabilityRule(
        id=f"{rule_type}-{params}",
        user_id="clinician",
        rule_type=rule_type,
        enforcement="hard",
        params=dict(params),
        appointment_type_id=appointment_type_id,
    )


def _hours(*weekdays: int) -> list[AvailabilityRule]:
    return [
        _rule(RuleType.WORKING_HOURS, day_of_week=d, start="09:00", end="17:00") for d in weekdays
    ]


class TestTheWorkingWeek:
    def test_no_hours_set_means_monday_to_friday(self) -> None:
        assert WorkingCalendar.from_rules([]).weekdays == MONDAY_TO_FRIDAY

    def test_is_the_weekdays_with_hours_set(self) -> None:
        # Monday, Tuesday and Thursday only.
        assert WorkingCalendar.from_rules(_hours(0, 1, 3)).weekdays == {0, 1, 3}

    def test_a_window_for_one_appointment_type_does_not_add_a_day(self) -> None:
        # Catches reading "intakes on Saturday" as the practice working Saturdays.
        rules = [
            *_hours(0, 1),
            _rule(RuleType.WORKING_HOURS, "intake", day_of_week=5, start="09:00", end="12:00"),
        ]
        assert WorkingCalendar.from_rules(rules).weekdays == {0, 1}

    def test_a_blocked_weekday_comes_off(self) -> None:
        rules = [_rule(RuleType.BLOCK_DAY_OF_WEEK, day_of_week=4)]
        assert WorkingCalendar.from_rules(rules).weekdays == {0, 1, 2, 3}

    def test_hours_only_on_blocked_days_falls_back_rather_than_leaving_no_week(self) -> None:
        rules = [*_hours(2), _rule(RuleType.BLOCK_DAY_OF_WEEK, day_of_week=2)]
        assert WorkingCalendar.from_rules(rules).weekdays == MONDAY_TO_FRIDAY


class TestCountingWorkingDays:
    def test_does_not_count_the_day_it_arrived(self) -> None:
        assert nth_working_day_after(MON, 2, WorkingCalendar()) == WED

    def test_across_a_weekend(self) -> None:
        # Thursday + 2 skips Saturday and Sunday.
        assert nth_working_day_after(THU, 2, WorkingCalendar()) == NEXT_MON

    def test_arriving_on_a_weekend_counts_from_the_next_working_day(self) -> None:
        assert nth_working_day_after(SAT, 2, WorkingCalendar()) == NEXT_TUE

    def test_across_a_weekend_and_a_holiday(self) -> None:
        # Friday + 2, with Monday a holiday the clinician marked away:
        # Saturday, Sunday and Monday are all skipped.
        calendar = WorkingCalendar.from_rules(
            [_rule(RuleType.BLOCK_SPECIFIC_DATES, dates=[NEXT_MON.isoformat()])]
        )
        assert nth_working_day_after(FRI, 2, calendar) == date(2026, 10, 21)

    def test_skips_an_away_range(self) -> None:
        calendar = WorkingCalendar.from_rules(
            [_rule(RuleType.BLOCK_DATE_RANGE, start_date=TUE.isoformat(), end_date=THU.isoformat())]
        )
        assert nth_working_day_after(MON, 1, calendar) == FRI

    def test_counts_only_the_clinicians_own_days(self) -> None:
        # Works Monday and Thursday: from Monday, two working days is next Monday.
        calendar = WorkingCalendar.from_rules(_hours(0, 3))
        assert nth_working_day_after(MON, 2, calendar) == NEXT_MON

    def test_away_for_longer_than_it_looks_still_gives_a_date(self) -> None:
        # Catches an endless search when the away range outruns the window.
        calendar = WorkingCalendar.from_rules(
            [_rule(RuleType.BLOCK_DATE_RANGE, start_date="2026-01-01", end_date="2099-12-31")]
        )
        assert nth_working_day_after(MON, 2, calendar) == WED

    def test_needs_at_least_one_day(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            nth_working_day_after(MON, 0, WorkingCalendar())


class TestDueAt:
    def test_is_the_end_of_the_due_day_in_the_clinicians_zone(self) -> None:
        submitted = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)  # Monday 11:00 in New York

        due = due_at(submitted, 2, WorkingCalendar(), NEW_YORK)

        assert due.tzinfo == UTC
        local = due.astimezone(NEW_YORK)
        assert (local.date(), local.hour, local.minute) == (WED, 23, 59)

    def test_the_day_it_arrived_is_the_clinicians_day_not_utcs(self) -> None:
        # 02:30 UTC Tuesday is still Monday evening in New York, so two
        # working days lands on Wednesday, not Thursday.
        submitted = datetime(2026, 10, 13, 2, 30, tzinfo=UTC)

        due = due_at(submitted, 2, WorkingCalendar(), NEW_YORK)

        assert due.astimezone(NEW_YORK).date() == WED
