# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One local calendar date as a run of real minutes.

The availability engine walks a day minute by minute. Those minutes are
counted in real time from the instant the date begins in the clinician's
zone, not read off the wall clock, so a day is 1440 minutes long except on
the two days a year the clock jumps: 1380 when it springs forward and 1500
when it falls back. Every local ``HH:MM`` a rule names reaches the axis
through :meth:`LocalDay.minute_of`, so working hours, blocked ranges and the
slots handed out all agree on which instant a wall time means.

A wall time the clock skips (02:30 on a spring-forward day) means the moment
the clock jumps, the end of the gap: a window starting or ending in the gap
starts or ends there, never later. A wall time the clock reads twice (01:30
on a fall-back day) means the first time it reads it, still on daylight time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo

DAY_MINUTES = 24 * 60


def _hhmm_to_minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def wall_instant(wall: datetime, tz: tzinfo) -> datetime:
    """The UTC instant a naive local wall time names in ``tz``.

    An ambiguous time resolves to its first occurrence (``fold=0``). A time
    in a spring-forward gap resolves to the end of the gap.
    """
    aware = wall.replace(tzinfo=tz, fold=0)
    instant = aware.astimezone(UTC)
    if instant.astimezone(tz).replace(tzinfo=None) == wall:
        return instant
    # In the gap, fold=0 reads the wall time with the offset from before the
    # jump (an instant after it) and fold=1 with the offset from after (an
    # instant before it). The jump lies between; find it.
    before = aware.replace(fold=1).astimezone(UTC)
    after_offset = instant.astimezone(tz).utcoffset()
    while after_offset is not None and instant - before > timedelta(seconds=1):
        mid = before + (instant - before) / 2
        if mid.astimezone(tz).utcoffset() == after_offset:
            instant = mid
        else:
            before = mid
    return instant.replace(microsecond=0)


@dataclass(frozen=True)
class LocalDay:
    """``day`` in ``tz``: the instant it starts and how many minutes it holds."""

    day: date
    tz: tzinfo
    start: datetime
    minutes: int

    @classmethod
    def of(cls, day: date, tz: tzinfo) -> LocalDay:
        start = wall_instant(datetime.combine(day, time(0)), tz)
        end = wall_instant(datetime.combine(day + timedelta(days=1), time(0)), tz)
        return cls(day, tz, start, int((end - start).total_seconds()) // 60)

    @property
    def end(self) -> datetime:
        return self.instant(self.minutes)

    def minute_of(self, wall: int | str) -> int:
        """The axis minute of a local wall time on this date.

        ``wall`` is ``HH:MM`` or minutes past local midnight; 24:00 is the
        next midnight, the end of the day.
        """
        wall_minute = _hhmm_to_minutes(wall) if isinstance(wall, str) else wall
        extra_days, minute_of_day = divmod(wall_minute, DAY_MINUTES)
        local = datetime.combine(
            self.day + timedelta(days=extra_days), time(*divmod(minute_of_day, 60))
        )
        return int((wall_instant(local, self.tz) - self.start).total_seconds()) // 60

    def span(self, start: int | str, end: int | str) -> tuple[int, int]:
        """A local wall-time range as half-open axis minutes."""
        return self.minute_of(start), self.minute_of(end)

    def instant(self, minute: int) -> datetime:
        """The aware UTC instant of an axis minute."""
        return self.start + timedelta(minutes=minute)

    def iso(self, minute: int) -> str:
        return self.instant(minute).strftime("%Y-%m-%dT%H:%M:%SZ")

    def hhmm(self, minute: int) -> str:
        """An axis minute as the local wall clock reads it; the day's end is 24:00."""
        local = self.instant(minute).astimezone(self.tz)
        hours = local.hour + 24 * (local.date() - self.day).days
        return f"{hours:02d}:{local.minute:02d}"

    def covered(self, start: datetime, end: datetime, before: int = 0, after: int = 0) -> set[int]:
        """The axis minutes ``[start, end)`` touches, widened by ``before``/``after``.

        Partial minutes round outward, and the result is clipped to the day.
        """
        first = math.floor((start - self.start).total_seconds() / 60) - before
        last = math.ceil((end - self.start).total_seconds() / 60) + after
        return set(range(max(first, 0), min(last, self.minutes)))


__all__ = ["DAY_MINUTES", "LocalDay", "wall_instant"]
