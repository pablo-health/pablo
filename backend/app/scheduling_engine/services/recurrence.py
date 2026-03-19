# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Recurrence generator — creates occurrence datetimes for recurring appointments."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from dateutil.rrule import MONTHLY, WEEKLY, rrule

from ..models.appointment import RecurrenceFrequency

_FREQ_MAP = {
    RecurrenceFrequency.WEEKLY: (WEEKLY, 1),
    RecurrenceFrequency.BIWEEKLY: (WEEKLY, 2),
    RecurrenceFrequency.MONTHLY: (MONTHLY, 1),
}

DEFAULT_HORIZON_DAYS = 180  # 6 months


class RecurrenceGenerator:
    """Generate occurrence start times for a recurring appointment.

    All datetimes are generated in the given timezone to handle DST transitions
    correctly (e.g., a 2pm Tuesday stays at 2pm local across DST changes),
    then converted back to UTC for storage.
    """

    @staticmethod
    def generate(
        start_at: datetime,
        frequency: RecurrenceFrequency,
        timezone: str,
        *,
        end_date: date | None = None,
        count: int | None = None,
    ) -> list[datetime]:
        """Generate occurrence start times.

        Args:
            start_at: First occurrence in UTC.
            frequency: weekly, biweekly, or monthly.
            timezone: IANA timezone (e.g., "America/New_York").
            end_date: Stop generating after this date (inclusive).
            count: Max number of occurrences (including the first).

        Returns:
            List of UTC datetimes for each occurrence.
        """
        tz = ZoneInfo(timezone)
        freq, interval = _FREQ_MAP[frequency]

        # Convert UTC start to local for generation
        local_start = start_at.astimezone(tz)

        # Default horizon: 6 months
        if end_date is None and count is None:
            end_date = (local_start + timedelta(days=DEFAULT_HORIZON_DAYS)).date()

        rule_kwargs: dict[str, object] = {
            "freq": freq,
            "interval": interval,
            "dtstart": local_start,
        }
        if end_date is not None:
            # rrule `until` is inclusive, set to end of day
            until_dt = datetime.combine(end_date, datetime.max.time()).replace(tzinfo=tz)
            rule_kwargs["until"] = until_dt
        if count is not None:
            rule_kwargs["count"] = count

        occurrences = list(rrule(**rule_kwargs))  # type: ignore[arg-type]

        # Convert each local occurrence back to UTC
        utc = ZoneInfo("UTC")
        return [dt.astimezone(utc).replace(tzinfo=None) for dt in occurrences]
