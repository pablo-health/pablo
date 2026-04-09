# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Availability engine — checks conflicts and computes free time slots."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..models.availability import RuleType
from ..models.conflict import Conflict, TimeSlot

if TYPE_CHECKING:
    from ..models.appointment import Appointment
    from ..models.availability import AvailabilityRule
    from ..repositories.appointment import AppointmentRepository
    from ..repositories.availability_rule import AvailabilityRuleRepository


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _time_to_minutes(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


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

    def check_conflicts(self, user_id: str, start_at: str, end_at: str) -> list[Conflict]:
        """Check all availability rules for conflicts with a proposed time."""
        rules = self._rule_repo.list_by_user(user_id)
        proposed_start = _parse_iso(start_at)
        proposed_end = _parse_iso(end_at)
        conflicts: list[Conflict] = []

        for rule in rules:
            conflict = self._check_rule(rule, user_id, proposed_start, proposed_end)
            if conflict:
                conflicts.append(conflict)

        return conflicts

    def get_free_slots(self, user_id: str, date_str: str, duration_minutes: int) -> list[TimeSlot]:
        """Compute available time slots for a given date and duration."""
        rules = self._rule_repo.list_by_user(user_id)

        working_ranges = self._get_working_hours(rules, date_str)
        if not working_ranges:
            return []

        if self._is_date_blocked(rules, date_str):
            return []

        blocked_minutes = self._get_blocked_minutes(rules)

        day_start = f"{date_str}T00:00:00Z"
        day_end = f"{date_str}T23:59:59Z"
        existing = self._appt_repo.list_by_range(user_id, day_start, day_end)
        active = [a for a in existing if a.status != "cancelled"]

        buffer_before, buffer_after = self._get_buffers(rules)

        appt_blocked = self._appointments_to_blocked_minutes(active, buffer_before, buffer_after)
        blocked_minutes = blocked_minutes | appt_blocked

        max_per_day = self._get_max_per_day(rules)
        if max_per_day is not None and len(active) >= max_per_day:
            return []

        slots: list[TimeSlot] = []
        remaining_capacity = max_per_day - len(active) if max_per_day is not None else None

        for work_start, work_end in working_ranges:
            minute = work_start
            while minute + duration_minutes <= work_end:
                slot_range = set(range(minute, minute + duration_minutes))
                if not slot_range & blocked_minutes:
                    start_h, start_m = divmod(minute, 60)
                    end_min = minute + duration_minutes
                    end_h, end_m = divmod(end_min, 60)
                    slot = TimeSlot(
                        start=f"{date_str}T{start_h:02d}:{start_m:02d}:00Z",
                        end=f"{date_str}T{end_h:02d}:{end_m:02d}:00Z",
                    )
                    slots.append(slot)
                    if remaining_capacity is not None:
                        remaining_capacity -= 1
                        if remaining_capacity <= 0:
                            return slots
                    minute += duration_minutes
                else:
                    minute += 1

        return slots

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
        date_str = proposed_start.strftime("%Y-%m-%d")
        day_start = f"{date_str}T00:00:00Z"
        day_end = f"{date_str}T23:59:59Z"
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
        date_str = proposed_start.strftime("%Y-%m-%d")
        day_start = f"{date_str}T00:00:00Z"
        day_end = proposed_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        nearby = self._appt_repo.list_by_range(user_id, day_start, day_end)
        for appt in nearby:
            if appt.status == "cancelled":
                continue
            appt_end = _parse_iso(appt.end_at)
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

        day_start = proposed_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        day_end = buffer_end.strftime("%Y-%m-%dT%H:%M:%SZ")
        nearby = self._appt_repo.list_by_range(user_id, day_start, day_end)
        for appt in nearby:
            if appt.status == "cancelled":
                continue
            appt_start = _parse_iso(appt.start_at)
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
        dt = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
        day_of_week = dt.weekday()
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
        dt = datetime.fromisoformat(f"{date_str}T00:00:00+00:00")
        day_of_week = dt.weekday()
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
    ) -> set[int]:
        """Convert existing appointments (with buffers) to blocked minutes."""
        blocked: set[int] = set()
        for appt in appointments:
            appt_start = _parse_iso(appt.start_at)
            appt_end = _parse_iso(appt.end_at)
            start_min = appt_start.hour * 60 + appt_start.minute - buffer_before
            end_min = appt_end.hour * 60 + appt_end.minute + buffer_after
            start_min = max(start_min, 0)
            end_min = min(end_min, 24 * 60)
            blocked.update(range(start_min, end_min))
        return blocked
