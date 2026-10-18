# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which days a clinician works, and when a target counted in them falls due.

A **working day** is a weekday the clinician has hours set on — the pattern
in their availability rules, not the days that happen to have appointments
booked. Bookings change week to week; a deadline counted against them would
move every time somebody booked or cancelled.

From that pattern three things come off:

* a weekday the clinician blocks outright,
* a date the clinician is away (a blocked date range or a list of blocked
  dates — which is also how a practice marks a holiday), and
* nothing else. Busy time, caps and buffers say how full a day is, not
  whether it is a working day.

**With no hours set at all, Monday to Friday.** A practice that has not set
up a calendar still has a working week, and that is the common one.

Pure: callers hand in the rules and a time zone, and nothing here reads a
clock or a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING

from ..scheduling_engine.models.availability import RuleType

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import tzinfo

    from ..scheduling_engine.models.availability import AvailabilityRule

#: Monday to Friday, as ``date.weekday()`` numbers them.
MONDAY_TO_FRIDAY: frozenset[int] = frozenset(range(5))

#: How far ahead to look for working days before giving up on away dates.
#: A year of being away is a calendar that has stopped describing a working
#: week, not a reason never to answer.
_SEARCH_LIMIT_DAYS = 366


@dataclass(frozen=True)
class WorkingCalendar:
    """The weekdays a clinician works and the dates they are away."""

    weekdays: frozenset[int] = MONDAY_TO_FRIDAY
    away_dates: frozenset[date] = frozenset()
    #: Inclusive ``(start, end)`` ranges the clinician is away.
    away_ranges: tuple[tuple[date, date], ...] = ()

    @classmethod
    def from_rules(cls, rules: Iterable[AvailabilityRule]) -> WorkingCalendar:
        """The calendar a clinician's availability rules describe.

        Only practice-wide hours count toward the working week: a window
        scoped to one appointment type narrows the practice's own hours on
        that day, it does not add a day the practice is otherwise closed.
        """
        hours: set[int] = set()
        blocked_weekdays: set[int] = set()
        away_dates: set[date] = set()
        away_ranges: list[tuple[date, date]] = []
        for rule in rules:
            params = rule.params
            if rule.rule_type == RuleType.WORKING_HOURS and rule.appointment_type_id is None:
                hours.add(int(params["day_of_week"]))
            elif rule.rule_type == RuleType.BLOCK_DAY_OF_WEEK:
                blocked_weekdays.add(int(params["day_of_week"]))
            elif rule.rule_type == RuleType.BLOCK_DATE_RANGE:
                away_ranges.append(
                    (
                        date.fromisoformat(params["start_date"]),
                        date.fromisoformat(params["end_date"]),
                    )
                )
            elif rule.rule_type == RuleType.BLOCK_SPECIFIC_DATES:
                away_dates.update(date.fromisoformat(d) for d in params.get("dates", []))
        weekdays = frozenset(hours or MONDAY_TO_FRIDAY) - blocked_weekdays
        return cls(
            # Hours set only on days that are also blocked leave no working
            # week at all; that is a calendar mid-edit, and Monday to Friday
            # is a better answer than none.
            weekdays=weekdays or MONDAY_TO_FRIDAY,
            away_dates=frozenset(away_dates),
            away_ranges=tuple(away_ranges),
        )

    def is_away(self, day: date) -> bool:
        return day in self.away_dates or any(start <= day <= end for start, end in self.away_ranges)

    def is_working_day(self, day: date) -> bool:
        return day.weekday() in self.weekdays and not self.is_away(day)


def nth_working_day_after(day: date, n: int, calendar: WorkingCalendar) -> date:
    """The *n*-th working day after *day*, not counting *day* itself.

    A request that arrives on a Monday with a target of two working days is
    due on Wednesday; one that arrives on Saturday is due on Tuesday. When
    away dates cover the whole search window, they are ignored rather than
    leaving the request with no due date.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    for honour_away in (True, False):
        found = 0
        current = day
        for _ in range(_SEARCH_LIMIT_DAYS * n):
            current += timedelta(days=1)
            working = (
                calendar.is_working_day(current)
                if honour_away
                else current.weekday() in calendar.weekdays
            )
            if working:
                found += 1
                if found == n:
                    return current
    raise AssertionError("a non-empty working week always has a working day")  # pragma: no cover


def due_at(submitted_at: datetime, days: int, calendar: WorkingCalendar, tz: tzinfo) -> datetime:
    """The end of the *days*-th working day after *submitted_at*, in UTC.

    The day boundary is the clinician's own: a request sent at 23:30 on a
    Monday in New York arrived on Monday there, whatever the date is in UTC.
    """
    local_day = submitted_at.astimezone(tz).date()
    due_day = nth_working_day_after(local_day, days, calendar)
    return datetime.combine(due_day, time(23, 59, 59), tzinfo=tz).astimezone(UTC)
