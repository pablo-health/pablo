# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A local date as real minutes, and which instant a local wall time names."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from app.scheduling_engine.services.local_day import LocalDay, wall_instant

NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize(
    ("day", "minutes"),
    [(date(2026, 3, 8), 23 * 60), (date(2026, 11, 1), 25 * 60), (date(2026, 6, 1), 24 * 60)],
)
def test_a_day_is_as_long_as_the_clock_runs(day: date, minutes: int) -> None:
    assert LocalDay.of(day, NY).minutes == minutes


@pytest.mark.parametrize(
    ("wall", "expected"),
    [
        # Skipped by the spring-forward jump: the jump itself, 03:00 EDT.
        ("2026-03-08T02:00", "2026-03-08T07:00:00+00:00"),
        ("2026-03-08T02:30", "2026-03-08T07:00:00+00:00"),
        # Read twice on the fall-back day: the first reading, on EDT.
        ("2026-11-01T01:30", "2026-11-01T05:30:00+00:00"),
        # Either side of each jump is unambiguous.
        ("2026-03-08T01:59", "2026-03-08T06:59:00+00:00"),
        ("2026-03-08T03:00", "2026-03-08T07:00:00+00:00"),
        ("2026-11-01T02:00", "2026-11-01T07:00:00+00:00"),
    ],
)
def test_a_wall_time_names_one_instant(wall: str, expected: str) -> None:
    assert wall_instant(datetime.fromisoformat(wall), NY) == datetime.fromisoformat(expected)


def test_the_axis_round_trips_on_an_ordinary_day() -> None:
    day = LocalDay.of(date(2026, 6, 1), NY)

    assert day.minute_of("09:30") == 570
    assert day.minute_of("24:00") == day.minutes
    assert day.hhmm(570) == "09:30"
    assert day.hhmm(day.minutes) == "24:00"
    assert day.instant(570) == datetime(2026, 6, 1, 13, 30, tzinfo=UTC)


def test_the_axis_counts_real_minutes_across_the_fall_back() -> None:
    day = LocalDay.of(date(2026, 11, 1), NY)

    # The first 01:00 is an hour in; 03:00 is three hours later, not two.
    assert day.minute_of("01:00") == 60
    assert day.minute_of("03:00") == 4 * 60
    assert day.iso(day.minute_of("03:00")) == "2026-11-01T08:00:00Z"
