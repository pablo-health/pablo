# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Availability engine — checks conflicts and computes free time slots."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import TYPE_CHECKING

from ..models.availability import RuleType
from ..models.conflict import Conflict, ConflictCheckResult, FreeSlotsResult, TimeSlot

if TYPE_CHECKING:
    from ..models.appointment import Appointment
    from ..models.availability import AvailabilityRule
    from ..repositories.appointment import AppointmentRepository
    from ..repositories.availability_rule import AvailabilityRuleRepository


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _local(dt: datetime | str, tz: tzinfo) -> datetime:
    """Resolve an instant to its wall-clock representation in ``tz``."""
    parsed = dt if isinstance(dt, datetime) else _parse_iso(dt)
    return parsed.astimezone(tz)


def _minute_to_utc_iso(day: date, minute: int, tz: tzinfo) -> str:
    """Render a local (day, minute-of-day) slot boundary as a UTC instant."""
    extra_days, minute_of_day = divmod(minute, 24 * 60)
    hour, mins = divmod(minute_of_day, 60)
    local_dt = datetime.combine(day + timedelta(days=extra_days), time(hour, mins), tzinfo=tz)
    return local_dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _time_to_minutes(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


DEFAULT_DURATION_MINUTES = 50

_ALIGNMENT_STEP_MINUTES = {"hour": 60, "half_hour": 30}


def _next_aligned_minute(minute: int, step: int) -> int:
    remainder = minute % step
    return minute if remainder == 0 else minute + (step - remainder)


class AvailabilityEngine:
    """Checks scheduling conflicts and computes free slots.

    Database-independent: operates through repository ABCs.
    """

    def __init__(
        self,
        rule_repo: AvailabilityRuleRepository,
        appointment_repo: AppointmentRepository,
    ) -> None:
        self._rule_repo = rule_repo
        self._appt_repo = appointment_repo

    def check_conflicts(
        self,
        user_id: str,
        start_at: str | datetime,
        end_at: str | datetime,
        *,
        tz: tzinfo = UTC,
    ) -> ConflictCheckResult:
        """Check all availability rules for conflicts with a proposed time.

        ``tz`` is the zone rules are evaluated in — weekday, hour, and date
        boundaries all read off the proposed time as seen in ``tz``, not UTC.
        Defaults to UTC so existing callers are unaffected.

        A user with zero rules is NOT CONFIGURED — ``configured`` is False,
        and ``conflicts`` is (necessarily) empty because there is nothing to
        check against. That is a permissive, not an approving, result: the
        caller should still let the booking through, but can use
        ``configured`` to show that availability hasn't been set up rather
        than treating the empty list as "checked and clear".
        """
        rules = self._rule_repo.list_by_user(user_id)
        proposed_start = _local(start_at, tz)
        proposed_end = _local(end_at, tz)
        conflicts: list[Conflict] = []

        for rule in rules:
            try:
                conflict = self._check_rule(rule, user_id, proposed_start, proposed_end)
            except (KeyError, TypeError, ValueError):
                # rule.params is an untyped dict with no validation at write time
                # (see AvailabilityRule) — a malformed rule is treated as
                # non-blocking rather than failing the whole check, since by
                # the time rules gate bookings a bad one must not take down
                # every other rule's evaluation, let alone the booking itself.
                continue
            if conflict:
                conflicts.append(conflict)

        return ConflictCheckResult(configured=bool(rules), conflicts=conflicts)

    def get_free_slots(
        self,
        user_id: str,
        date_str: str,
        duration_minutes: int | None = None,
        *,
        tz: tzinfo = UTC,
    ) -> FreeSlotsResult:
        """Compute available time slots for a given date and duration.

        ``date_str`` is a local calendar date in ``tz`` — the working-hours
        window runs from local midnight to the next local midnight, and slot
        boundaries are computed in ``tz`` before being rendered as UTC
        instants. Defaults to UTC so existing callers are unaffected.

        ``duration_minutes`` of None resolves from the user's session_defaults
        rule (falling back to :data:`DEFAULT_DURATION_MINUTES`); callers that
        pass a duration explicitly keep that exact value.

        A user with zero rules is NOT CONFIGURED — ``configured`` is False,
        distinct from a configured user whose rules simply leave no openings
        on this date. Both cases produce an empty ``slots`` list, so callers
        must check ``configured`` to tell "set up your availability" apart
        from "this day is full".
        """
        rules = self._rule_repo.list_by_user(user_id)
        resolved_duration = (
            duration_minutes if duration_minutes is not None else self._get_default_duration(rules)
        )
        if not rules:
            return FreeSlotsResult(configured=False, slots=[], duration_minutes=resolved_duration)

        working_ranges = self._get_working_hours(rules, date_str)
        if not working_ranges:
            return FreeSlotsResult(configured=True, slots=[], duration_minutes=resolved_duration)

        if self._is_date_blocked(rules, date_str):
            return FreeSlotsResult(configured=True, slots=[], duration_minutes=resolved_duration)

        blocked_minutes = self._get_blocked_minutes(rules)

        day = date.fromisoformat(date_str)
        day_start = datetime.combine(day, time(0), tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        existing = self._appt_repo.list_by_range(user_id, day_start, day_end)
        active = [a for a in existing if a.status != "cancelled"]

        buffer_before, buffer_after = self._get_buffers(rules)

        appt_blocked = self._appointments_to_blocked_minutes(
            active, buffer_before, buffer_after, tz
        )
        blocked_minutes = blocked_minutes | appt_blocked

        max_per_day = self._get_max_per_day(rules)
        if max_per_day is not None and len(active) >= max_per_day:
            return FreeSlotsResult(configured=True, slots=[], duration_minutes=resolved_duration)

        alignment_step = self._get_alignment_step(rules)

        slots: list[TimeSlot] = []
        remaining_capacity = max_per_day - len(active) if max_per_day is not None else None

        for work_start, work_end in working_ranges:
            minute = (
                _next_aligned_minute(work_start, alignment_step) if alignment_step else work_start
            )
            while minute + resolved_duration <= work_end:
                slot_range = set(range(minute, minute + resolved_duration))
                if not slot_range & blocked_minutes:
                    slot = TimeSlot(
                        start=_minute_to_utc_iso(day, minute, tz),
                        end=_minute_to_utc_iso(day, minute + resolved_duration, tz),
                    )
                    slots.append(slot)
                    if remaining_capacity is not None:
                        remaining_capacity -= 1
                        if remaining_capacity <= 0:
                            return FreeSlotsResult(
                                configured=True, slots=slots, duration_minutes=resolved_duration
                            )
                    minute += resolved_duration + buffer_before + buffer_after
                    if alignment_step:
                        minute = _next_aligned_minute(minute, alignment_step)
                elif alignment_step:
                    minute = _next_aligned_minute(minute + 1, alignment_step)
                else:
                    minute += 1

        return FreeSlotsResult(configured=True, slots=slots, duration_minutes=resolved_duration)

    def _check_rule(
        self,
        rule: AvailabilityRule,
        user_id: str,
        proposed_start: datetime,
        proposed_end: datetime,
    ) -> Conflict | None:
        """Check a single rule against a proposed time window."""
        checkers = {
            RuleType.WORKING_HOURS: lambda: self._check_working_hours(
                rule, proposed_start, proposed_end
            ),
            RuleType.BLOCK_DAY_OF_WEEK: lambda: self._check_block_day_of_week(rule, proposed_start),
            RuleType.BLOCK_TIME_RANGE: lambda: self._check_block_time_range(
                rule, proposed_start, proposed_end
            ),
            RuleType.MAX_PER_DAY: lambda: self._check_max_per_day(rule, user_id, proposed_start),
            RuleType.BUFFER_BEFORE: lambda: self._check_buffer_before(
                rule, user_id, proposed_start, rule.params
            ),
            RuleType.BUFFER_AFTER: lambda: self._check_buffer_after(
                rule, user_id, proposed_end, rule.params
            ),
            RuleType.BLOCK_DATE_RANGE: lambda: self._check_block_date_range(rule, proposed_start),
            RuleType.BLOCK_SPECIFIC_DATES: lambda: self._check_block_specific_dates(
                rule, proposed_start
            ),
        }
        checker = checkers.get(RuleType(rule.rule_type))
        return checker() if checker else None

    def _check_working_hours(
        self,
        rule: AvailabilityRule,
        proposed_start: datetime,
        proposed_end: datetime,
    ) -> Conflict | None:
        day_of_week = rule.params["day_of_week"]
        if proposed_start.weekday() != day_of_week:
            return None

        work_start = _time_to_minutes(rule.params["start"])
        work_end = _time_to_minutes(rule.params["end"])
        prop_start_min = proposed_start.hour * 60 + proposed_start.minute
        prop_end_min = proposed_end.hour * 60 + proposed_end.minute

        if prop_start_min >= work_start and prop_end_min <= work_end:
            return None

        return Conflict(
            rule=rule,
            enforcement=rule.enforcement,
            message=f"Outside working hours ({rule.params['start']}-{rule.params['end']})",
        )

    def _check_block_day_of_week(
        self, rule: AvailabilityRule, proposed_start: datetime
    ) -> Conflict | None:
        if proposed_start.weekday() == rule.params["day_of_week"]:
            return Conflict(
                rule=rule,
                enforcement=rule.enforcement,
                message=f"Day of week {proposed_start.weekday()} is blocked",
            )
        return None

    def _check_block_time_range(
        self,
        rule: AvailabilityRule,
        proposed_start: datetime,
        proposed_end: datetime,
    ) -> Conflict | None:
        block_start = _time_to_minutes(rule.params["start"])
        block_end = _time_to_minutes(rule.params["end"])
        prop_start = proposed_start.hour * 60 + proposed_start.minute
        prop_end = proposed_end.hour * 60 + proposed_end.minute

        if _ranges_overlap(prop_start, prop_end, block_start, block_end):
            return Conflict(
                rule=rule,
                enforcement=rule.enforcement,
                message=f"Overlaps blocked time range {rule.params['start']}-{rule.params['end']}",
            )
        return None

    def _check_max_per_day(
        self,
        rule: AvailabilityRule,
        user_id: str,
        proposed_start: datetime,
    ) -> Conflict | None:
        day_start = datetime.combine(proposed_start.date(), time(0), tzinfo=proposed_start.tzinfo)
        day_end = day_start + timedelta(days=1)
        existing = self._appt_repo.list_by_range(user_id, day_start, day_end)
        active = [a for a in existing if a.status != "cancelled"]
        max_count = rule.params["max"]
        if len(active) >= max_count:
            return Conflict(
                rule=rule,
                enforcement=rule.enforcement,
                message=(
                    f"Maximum {max_count} appointments per day reached ({len(active)} existing)"
                ),
            )
        return None

    def _check_buffer_before(
        self,
        rule: AvailabilityRule,
        user_id: str,
        proposed_start: datetime,
        params: dict[str, int],
    ) -> Conflict | None:
        buffer_minutes = params["minutes"]
        buffer_start = proposed_start - timedelta(minutes=buffer_minutes)

        # Find appointments that could end within the buffer window.
        # We need appointments whose end_at > buffer_start, so search
        # with a wide start range to capture them.
        day_start = datetime.combine(proposed_start.date(), time(0), tzinfo=proposed_start.tzinfo)
        nearby = self._appt_repo.list_by_range(user_id, day_start, proposed_start)
        for appt in nearby:
            if appt.status == "cancelled":
                continue
            appt_end = appt.end_at
            if appt_end > buffer_start:
                return Conflict(
                    rule=rule,
                    enforcement=rule.enforcement,
                    message=f"Violates {buffer_minutes}-minute buffer before appointment",
                )
        return None

    def _check_buffer_after(
        self,
        rule: AvailabilityRule,
        user_id: str,
        proposed_end: datetime,
        params: dict[str, int],
    ) -> Conflict | None:
        buffer_minutes = params["minutes"]
        buffer_end = proposed_end + timedelta(minutes=buffer_minutes)

        nearby = self._appt_repo.list_by_range(user_id, proposed_end, buffer_end)
        for appt in nearby:
            if appt.status == "cancelled":
                continue
            appt_start = appt.start_at
            if appt_start < buffer_end:
                return Conflict(
                    rule=rule,
                    enforcement=rule.enforcement,
                    message=f"Violates {buffer_minutes}-minute buffer after appointment",
                )
        return None

    def _check_block_date_range(
        self, rule: AvailabilityRule, proposed_start: datetime
    ) -> Conflict | None:
        start_date = rule.params["start_date"]
        end_date = rule.params["end_date"]
        date_str = proposed_start.strftime("%Y-%m-%d")
        if start_date <= date_str <= end_date:
            return Conflict(
                rule=rule,
                enforcement=rule.enforcement,
                message=f"Date falls in blocked range {start_date} to {end_date}",
            )
        return None

    def _check_block_specific_dates(
        self, rule: AvailabilityRule, proposed_start: datetime
    ) -> Conflict | None:
        dates: list[str] = rule.params["dates"]
        date_str = proposed_start.strftime("%Y-%m-%d")
        if date_str in dates:
            return Conflict(
                rule=rule,
                enforcement=rule.enforcement,
                message=f"Date {date_str} is specifically blocked",
            )
        return None

    # --- Free slots helpers ---

    def _get_working_hours(
        self, rules: list[AvailabilityRule], date_str: str
    ) -> list[tuple[int, int]]:
        """Get working hour ranges (in minutes) for a given date."""
        day_of_week = date.fromisoformat(date_str).weekday()
        ranges: list[tuple[int, int]] = []
        for rule in rules:
            if (
                rule.rule_type == RuleType.WORKING_HOURS
                and rule.params.get("day_of_week") == day_of_week
            ):
                start = _time_to_minutes(rule.params["start"])
                end = _time_to_minutes(rule.params["end"])
                ranges.append((start, end))
        return sorted(ranges)

    def _is_date_blocked(self, rules: list[AvailabilityRule], date_str: str) -> bool:
        day_of_week = date.fromisoformat(date_str).weekday()
        for rule in rules:
            if (
                rule.rule_type == RuleType.BLOCK_DAY_OF_WEEK
                and rule.params.get("day_of_week") == day_of_week
            ):
                return True
            if (
                rule.rule_type == RuleType.BLOCK_DATE_RANGE
                and rule.params["start_date"] <= date_str <= rule.params["end_date"]
            ):
                return True
            if rule.rule_type == RuleType.BLOCK_SPECIFIC_DATES and date_str in rule.params.get(
                "dates", []
            ):
                return True
        return False

    def _get_blocked_minutes(self, rules: list[AvailabilityRule]) -> set[int]:
        """Get blocked minutes from block_time_range rules."""
        blocked: set[int] = set()
        for rule in rules:
            if rule.rule_type == RuleType.BLOCK_TIME_RANGE:
                start = _time_to_minutes(rule.params["start"])
                end = _time_to_minutes(rule.params["end"])
                blocked.update(range(start, end))
        return blocked

    def _get_buffers(self, rules: list[AvailabilityRule]) -> tuple[int, int]:
        """Get buffer before and after values from rules."""
        buffer_before = 0
        buffer_after = 0
        for rule in rules:
            if rule.rule_type == RuleType.BUFFER_BEFORE:
                buffer_before = max(buffer_before, rule.params["minutes"])
            elif rule.rule_type == RuleType.BUFFER_AFTER:
                buffer_after = max(buffer_after, rule.params["minutes"])
        return buffer_before, buffer_after

    def _get_session_defaults_rule(self, rules: list[AvailabilityRule]) -> AvailabilityRule | None:
        """Get the user's session_defaults rule, if any (first by created_at)."""
        for rule in rules:
            if rule.rule_type == RuleType.SESSION_DEFAULTS:
                return rule
        return None

    def _get_default_duration(self, rules: list[AvailabilityRule]) -> int:
        """Resolve the fallback slot duration from the session_defaults rule."""
        rule = self._get_session_defaults_rule(rules)
        if rule is not None:
            duration = rule.params.get("duration_minutes")
            if duration is not None:
                return int(duration)
        return DEFAULT_DURATION_MINUTES

    def _get_alignment_step(self, rules: list[AvailabilityRule]) -> int:
        """Resolve the start-time alignment grid (in minutes), 0 for none."""
        rule = self._get_session_defaults_rule(rules)
        if rule is None:
            return 0
        alignment = rule.params.get("alignment")
        if not isinstance(alignment, str):
            return 0
        return _ALIGNMENT_STEP_MINUTES.get(alignment, 0)

    def _get_max_per_day(self, rules: list[AvailabilityRule]) -> int | None:
        """Get the most restrictive max_per_day value."""
        result: int | None = None
        for rule in rules:
            if rule.rule_type == RuleType.MAX_PER_DAY:
                max_val = rule.params["max"]
                if result is None or max_val < result:
                    result = max_val
        return result

    def _appointments_to_blocked_minutes(
        self,
        appointments: list[Appointment],
        buffer_before: int,
        buffer_after: int,
        tz: tzinfo,
    ) -> set[int]:
        """Convert existing appointments (with buffers) to blocked minutes."""
        blocked: set[int] = set()
        for appt in appointments:
            appt_start = _local(appt.start_at, tz)
            appt_end = _local(appt.end_at, tz)
            start_min = appt_start.hour * 60 + appt_start.minute - buffer_before
            end_min = appt_end.hour * 60 + appt_end.minute + buffer_after
            start_min = max(start_min, 0)
            end_min = min(end_min, 24 * 60)
            blocked.update(range(start_min, end_min))
        return blocked
