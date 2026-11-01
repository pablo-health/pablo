# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Availability engine — checks conflicts and computes free time slots."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import TYPE_CHECKING

from ..models.availability import EnforcementLevel, RuleType
from ..models.busy import BusyKind
from ..models.conflict import Conflict, ConflictCheckResult, FreeSlotsResult, TimeSlot
from ..models.floating_block import ResolvedFloatingBlock
from . import floating_block
from .local_day import LocalDay

if TYPE_CHECKING:
    from ..models.appointment import Appointment
    from ..models.availability import AvailabilityRule
    from ..models.busy import BusyInterval, BusyTimeSource
    from ..repositories.appointment import AppointmentRepository
    from ..repositories.availability_rule import AvailabilityRuleRepository


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _local(dt: datetime | str, tz: tzinfo) -> datetime:
    """Resolve an instant to its wall-clock representation in ``tz``.

    Offset-less input is read as wall-clock in ``tz`` rather than converted.
    ``astimezone`` would otherwise resolve a naive datetime against the
    *host's* timezone, so the same rule check would land on a different hour
    on a UTC container than on a developer's laptop.
    """
    parsed = dt if isinstance(dt, datetime) else _parse_iso(dt)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _time_to_minutes(t: str) -> int:
    """Convert 'HH:MM' to minutes since midnight."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _ranges_overlap(start_a: int, end_a: int, start_b: int, end_b: int) -> bool:
    return start_a < end_b and start_b < end_a


def _subtract_spans(
    span: tuple[datetime, datetime], cuts: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """What is left of ``span`` once every span in ``cuts`` is taken out."""
    remaining = [span]
    for cut_start, cut_end in cuts:
        pieces: list[tuple[datetime, datetime]] = []
        for start, end in remaining:
            if cut_end <= start or end <= cut_start:
                pieces.append((start, end))
                continue
            if start < cut_start:
                pieces.append((start, cut_start))
            if cut_end < end:
                pieces.append((cut_end, end))
        remaining = pieces
    return remaining


#: What the clinician is told when a proposed time overlaps busy time. The
#: kind is the only detail given: a free/busy answer has nothing else in it.
_BUSY_MESSAGES = {
    BusyKind.CALENDAR: "Your calendar shows you as busy at this time",
    BusyKind.OUTSIDE_SESSION: (
        "Overlaps a session on your calendar you haven't told Pablo about yet"
    ),
}


DEFAULT_DURATION_MINUTES = 50

_ALIGNMENT_STEP_MINUTES = {"hour": 60, "half_hour": 30}


def _next_aligned_minute(minute: int, step: int) -> int:
    remainder = minute % step
    return minute if remainder == 0 else minute + (step - remainder)


class _DayCap(StrEnum):
    """What the day's max_per_day rules, taken together, have decided."""

    OPEN = "open"
    OVER = "over"
    CLOSED = "closed"


def _applies_to_type(rule: AvailabilityRule, appointment_type_id: str | None) -> bool:
    """Whether ``rule`` governs a listing for ``appointment_type_id``.

    A practice-wide rule governs every type, which is why an unscoped rule
    set behaves exactly as it did before types entered the picture.
    """
    return rule.appointment_type_id is None or rule.appointment_type_id == appointment_type_id


def _sets_up_availability(rules: list[AvailabilityRule]) -> bool:
    """Whether any rule says something about when the clinician can be booked.

    A floating block alone does not: it protects time inside availability
    that has not been set up yet, so a clinician whose only rule is a lunch
    block still reads as not configured, exactly as before they added it.
    """
    return any(r.rule_type != RuleType.FLOATING_BLOCK for r in rules)


def _breaks_a_floating_block(
    rules: list[AvailabilityRule], day: LocalDay, occupied: set[int], span: set[int]
) -> bool:
    """Whether booking ``span`` would leave any floating block no room.

    A rule whose params cannot be read is skipped, the engine's usual stance
    on an unvalidated rule (see :meth:`AvailabilityEngine.check_conflicts`).
    """
    for rule in rules:
        try:
            if floating_block.breaks_block(rule, day, occupied, span):
                return True
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return False


def _intersect_ranges(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Every span present in both lists of half-open minute ranges."""
    overlaps = [
        (max(a_start, b_start), min(a_end, b_end))
        for a_start, a_end in a
        for b_start, b_end in b
        if _ranges_overlap(a_start, a_end, b_start, b_end)
    ]
    return sorted(overlaps)


def _window_minutes(rule: AvailabilityRule, day: LocalDay | None = None) -> set[int]:
    """The minutes a working_hours rule covers: on ``day``'s axis, or wall-clock without one."""
    if day is not None:
        return set(range(*day.span(rule.params["start"], rule.params["end"])))
    return set(range(_time_to_minutes(rule.params["start"]), _time_to_minutes(rule.params["end"])))


def _is_exclusive_window(rule: AvailabilityRule) -> bool:
    """Whether ``rule`` claims its window for one type and no other.

    Only working_hours defines a window to claim. A rule that counts or
    blocks has nothing to hand out, so ``allow_other_types`` means nothing
    on one and is ignored rather than guessed at.
    """
    return (
        rule.rule_type == RuleType.WORKING_HOURS
        and rule.appointment_type_id is not None
        and not rule.allow_other_types
    )


class AvailabilityEngine:
    """Checks scheduling conflicts and computes free slots.

    Database-independent: operates through repository ABCs.
    """

    def __init__(
        self,
        rule_repo: AvailabilityRuleRepository,
        appointment_repo: AppointmentRepository,
        busy_source: BusyTimeSource | None = None,
    ) -> None:
        """``busy_source`` is optional so a caller that has none keeps working.

        Without one the engine answers from rules and appointments alone, as
        it always has. With one, :meth:`get_free_slots` leaves out every slot
        that overlaps a busy window, and :meth:`check_conflicts` can report
        the overlap when asked to.
        """
        self._rule_repo = rule_repo
        self._appt_repo = appointment_repo
        self._busy_source = busy_source

    def check_conflicts(
        self,
        user_id: str,
        start_at: str | datetime,
        end_at: str | datetime,
        *,
        tz: tzinfo = UTC,
        include_busy: bool = False,
    ) -> ConflictCheckResult:
        """Check all availability rules for conflicts with a proposed time.

        ``tz`` is the zone rules are evaluated in — weekday, hour, and date
        boundaries all read off the proposed time as seen in ``tz``, not UTC.
        Defaults to UTC so existing callers are unaffected.

        ``include_busy`` also reports an overlap with the busy source's
        windows, as a SOFT conflict with no rule. It is off by default
        because the booking write path calls this too, and busy time never
        stops a clinician booking a time by hand — it is shown to them before
        they save, which is the one caller that asks for it.

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
        hours_checked = False

        for rule in rules:
            if rule.rule_type == RuleType.WORKING_HOURS and rule.appointment_type_id is None:
                # A day's ranges are one answer, not one per range: see
                # _check_working_hours. Asked once, where the first one sits.
                if not hours_checked:
                    hours_checked = True
                    conflict = self._check_working_hours(rules, proposed_start, proposed_end)
                    if conflict:
                        conflicts.append(conflict)
                continue
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

        if include_busy:
            conflicts.extend(self._busy_conflicts(user_id, rules, proposed_start, proposed_end, tz))

        return ConflictCheckResult(configured=_sets_up_availability(rules), conflicts=conflicts)

    def _busy_conflicts(
        self,
        user_id: str,
        rules: list[AvailabilityRule],
        proposed_start: datetime,
        proposed_end: datetime,
        tz: tzinfo,
    ) -> list[Conflict]:
        """One SOFT conflict per kind of busy time the proposed window overlaps.

        Busy time is held to the clinician's buffers, as their appointments
        are: a proposed time that leaves less than the gap they keep before
        or after a busy window is reported too. The buffers are the
        practice-wide ones, because this check is not type-scoped (see
        :meth:`_check_rule`).

        Time Pablo's own appointments already fill is taken out of each busy
        window BEFORE the buffers go round what is left. Those appointments
        are on the connected calendar too, so otherwise an appointment being
        edited or nudged would report its own event, or its own buffer, back
        as busy.
        """
        if self._busy_source is None or proposed_end <= proposed_start:
            return []
        try:
            buffer_before, buffer_after = self._get_buffers(
                [r for r in rules if r.appointment_type_id is None]
            )
        except (KeyError, TypeError, ValueError):
            # The same stance check_conflicts takes on a rule it cannot read.
            buffer_before, buffer_after = 0, 0
        before = timedelta(minutes=buffer_before)
        after = timedelta(minutes=buffer_after)
        # Wide enough to catch a window whose buffer, not its own span,
        # reaches into the proposed time.
        reach_start, reach_end = proposed_start - after, proposed_end + before
        windows = self._busy_source.busy_between(user_id, reach_start, reach_end)
        if not windows:
            return []
        booked = [
            (_local(a.start_at, tz), _local(a.end_at, tz))
            for a in self._appt_repo.list_by_range(user_id, reach_start, reach_end)
            if a.status != "cancelled"
        ]
        kinds: list[BusyKind] = []
        for window in windows:
            if window.kind in kinds:
                continue
            span = (_local(window.start, tz), _local(window.end, tz))
            for start, end in _subtract_spans(span, booked):
                if start - before < proposed_end and proposed_start < end + after:
                    kinds.append(window.kind)
                    break
        return [
            Conflict(rule=None, enforcement=EnforcementLevel.SOFT, message=_BUSY_MESSAGES[kind])
            for kind in sorted(kinds)
        ]

    def get_free_slots(
        self,
        user_id: str,
        date_str: str,
        duration_minutes: int | None = None,
        *,
        tz: tzinfo = UTC,
        appointment_type_id: str | None = None,
    ) -> FreeSlotsResult:
        """Compute available time slots for a given date, duration and type.

        ``date_str`` is a local calendar date in ``tz`` — the working-hours
        window runs from local midnight to the next local midnight, and slot
        boundaries are computed in ``tz`` before being rendered as UTC
        instants. Defaults to UTC so existing callers are unaffected.

        ``duration_minutes`` of None resolves from the user's session_defaults
        rule (falling back to :data:`DEFAULT_DURATION_MINUTES`); callers that
        pass a duration explicitly keep that exact value.

        ``appointment_type_id`` is the kind of appointment being listed for.
        A rule applies when its own ``appointment_type_id`` is None (the
        practice-wide rule) or equals this one; rules scoped to a different
        type are skipped. Passing None therefore lists exactly what a
        practice-wide rule set has always produced.

        WHEN A PRACTICE-WIDE AND A TYPE-SCOPED RULE OF THE SAME TYPE BOTH
        APPLY, the answer is fixed per rule type and never left to whichever
        row was written first:

        * ``working_hours`` — INTERSECTION. The practice's own hours are the
          outer bound and a type-scoped window can only narrow within them; a
          type is never offered outside the hours the practice keeps. A
          type-scoped window on a weekday the practice does not work
          therefore yields nothing, because the practice is shut.
        * ``buffer_before`` / ``buffer_after`` — THE LARGER WINS, as it
          already does between two practice-wide buffers. A buffer is a
          minimum gap, and the longest minimum is the one that holds.
        * ``max_per_day`` — BOTH COUNT, INDEPENDENTLY, over different
          populations. A practice-wide cap counts every active appointment
          that day; a type-scoped cap counts only that type's. "Two intakes a
          day" does not exempt an intake from "eight appointments a day" —
          whichever is reached first closes the day (HARD) or marks the
          remaining slots ``over_cap`` (SOFT).
        * everything that blocks (``block_time_range``, ``block_day_of_week``,
          ``block_date_range``, ``block_specific_dates``) — UNION. Every
          applying rule subtracts, which is what these rules already do
          between themselves.
        * ``session_defaults`` — the type-scoped one wins outright if there is
          one, since a default only has one value to give.

        EXCLUSIVE WINDOWS. A type-scoped ``working_hours`` rule with
        ``allow_other_types`` False claims its window: it is subtracted from
        every OTHER type's listing. HARD removes those slots; SOFT returns
        them marked ``over_cap``, the same flag and the same enforcement
        ladder a soft day cap uses, so a practice can still offer them
        deliberately in-app while a public surface hides them. Two types
        whose exclusive windows overlap both lose the overlap — each claim
        subtracts from the other, so the result does not depend on rule
        ordering or creation time. A listing with no type named
        (``appointment_type_id`` None) has every exclusive window subtracted,
        because it cannot promise the slot will be used by the type that
        claimed it.

        Exclusivity governs LISTING ONLY. It never touches appointments
        already booked in a window that later became exclusive, and it does
        not stand between a practice and a booking it makes directly —
        :meth:`check_conflicts` is the booking-side gate and is deliberately
        not type-scoped.

        A user with zero rules is NOT CONFIGURED — ``configured`` is False,
        distinct from a configured user whose rules simply leave no openings
        on this date. Both cases produce an empty ``slots`` list, so callers
        must check ``configured`` to tell "set up your availability" apart
        from "this day is full".
        """
        all_rules = self._rule_repo.list_by_user(user_id)
        rules = [r for r in all_rules if _applies_to_type(r, appointment_type_id)]
        resolved_duration = (
            duration_minutes if duration_minutes is not None else self._get_default_duration(rules)
        )
        if not _sets_up_availability(all_rules):
            return FreeSlotsResult(configured=False, slots=[], duration_minutes=resolved_duration)

        # Every minute below is a real minute since the date began in ``tz``
        # (see ``local_day``), so a daylight-saving day is 23 or 25 hours.
        day = LocalDay.of(date.fromisoformat(date_str), tz)
        working_ranges = self._get_working_hours(rules, day, appointment_type_id)
        if not working_ranges:
            return FreeSlotsResult(configured=True, slots=[], duration_minutes=resolved_duration)

        if self._is_date_blocked(rules, date_str):
            return FreeSlotsResult(configured=True, slots=[], duration_minutes=resolved_duration)

        blocked_minutes = self._get_blocked_minutes(rules, day)
        hard_claimed, soft_claimed = self._claimed_minutes(all_rules, day, appointment_type_id)
        blocked_minutes = blocked_minutes | hard_claimed

        existing = self._appt_repo.list_by_range(user_id, day.start, day.end)
        active = [a for a in existing if a.status != "cancelled"]

        buffer_before, buffer_after = self._get_buffers(rules)

        appt_blocked = _appointments_to_blocked_minutes(active, day, buffer_before, buffer_after)
        blocked_minutes = blocked_minutes | appt_blocked

        capped = self._day_cap_state(rules, active, appointment_type_id)
        if capped is _DayCap.CLOSED:
            return FreeSlotsResult(configured=True, slots=[], duration_minutes=resolved_duration)
        over_cap = capped is _DayCap.OVER

        busy: list[BusyInterval] = []
        if self._busy_source is not None:
            # Asked for only once the day has hours left to offer, so a
            # closed or full day never costs a calendar read. Widened by the
            # buffers, so a window just outside the day whose buffer reaches
            # into it still counts.
            busy = self._busy_source.busy_between(
                user_id,
                day.start - timedelta(minutes=buffer_after),
                day.end + timedelta(minutes=buffer_before),
            )
            blocked_minutes = blocked_minutes | _busy_to_blocked_minutes(
                busy, day, buffer_before, buffer_after
            )

        # A floating block is checked per candidate: whether a slot breaks it
        # depends on where the rest of the day's free time is, so it cannot
        # be folded into blocked_minutes up front. What it counts as taken is
        # appointments and busy time without their buffers (see
        # ``floating_block``).
        floating = [r for r in rules if r.rule_type == RuleType.FLOATING_BLOCK]
        occupied = (
            _appointments_to_blocked_minutes(active, day) | _busy_to_blocked_minutes(busy, day)
            if floating
            else set()
        )

        alignment_step = self._get_alignment_step(rules)

        slots: list[TimeSlot] = []

        for work_start, work_end in working_ranges:
            minute = (
                _next_aligned_minute(work_start, alignment_step) if alignment_step else work_start
            )
            while minute + resolved_duration <= work_end:
                slot_range = set(range(minute, minute + resolved_duration))
                if not slot_range & blocked_minutes and not _breaks_a_floating_block(
                    floating, day, occupied, slot_range
                ):
                    slot = TimeSlot(
                        start=day.iso(minute),
                        end=day.iso(minute + resolved_duration),
                        over_cap=over_cap or bool(slot_range & soft_claimed),
                    )
                    slots.append(slot)
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
        """Check a single rule against a proposed time window.

        TYPE-SCOPED RULES ARE SKIPPED HERE. A conflict check is handed an
        instant and nothing else — it does not know what kind of appointment
        is being proposed, so it cannot tell whether "at most two intakes a
        day" is even about this booking. Applying it anyway would count every
        appointment against a cap meant for one type. Type scoping is a
        listing concept (see :meth:`get_free_slots`); the booking-side gate
        stays practice-wide, which is also what keeps a practice free to book
        deliberately inside a window some other type has claimed.
        """
        if rule.appointment_type_id is not None:
            return None

        checkers = {
            # WORKING_HOURS is not here: a day's ranges are checked together,
            # once, by check_conflicts (_check_working_hours).
            RuleType.BLOCK_DAY_OF_WEEK: lambda: self._check_block_day_of_week(rule, proposed_start),
            RuleType.BLOCK_TIME_RANGE: lambda: self._check_block_time_range(
                rule, proposed_start, proposed_end
            ),
            RuleType.MAX_PER_DAY: lambda: self._check_max_per_day(rule, user_id, proposed_start),
            RuleType.MAX_PER_WEEK: lambda: self._check_max_per_week(rule, user_id, proposed_start),
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
            RuleType.FLOATING_BLOCK: lambda: self._check_floating_block(
                rule, user_id, proposed_start, proposed_end
            ),
        }
        checker = checkers.get(RuleType(rule.rule_type))
        return checker() if checker else None

    def _check_working_hours(
        self,
        rules: list[AvailabilityRule],
        proposed_start: datetime,
        proposed_end: datetime,
    ) -> Conflict | None:
        """Whether the time sits inside one of the day's working ranges.

        A day can have several ranges (9-12 and 2-6). The time is inside
        working hours when any one of them holds all of it, which is how
        :meth:`get_free_slots` lists slots, range by range. Checking each
        range on its own would put every time on a split day outside one of
        them. With no range for the weekday there is nothing to check, as
        before. A rule whose params cannot be read is skipped.
        """
        prop_start_min = proposed_start.hour * 60 + proposed_start.minute
        prop_end_min = proposed_end.hour * 60 + proposed_end.minute
        ranges: list[tuple[int, int, AvailabilityRule]] = []
        for rule in rules:
            if rule.rule_type != RuleType.WORKING_HOURS or rule.appointment_type_id is not None:
                continue
            try:
                if rule.params["day_of_week"] != proposed_start.weekday():
                    continue
                span = (
                    _time_to_minutes(rule.params["start"]),
                    _time_to_minutes(rule.params["end"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            ranges.append((*span, rule))
        if not ranges:
            return None
        if any(start <= prop_start_min and prop_end_min <= end for start, end, _ in ranges):
            return None

        ranges.sort(key=lambda r: r[0])
        # The strictest range speaks for the day: a hard one is not softened by
        # a soft one beside it (the same reading as the daily cap's).
        hard = [r for _, _, r in ranges if r.enforcement == EnforcementLevel.HARD]
        rule = hard[0] if hard else ranges[0][2]
        spans = ", ".join(f"{r.params['start']}-{r.params['end']}" for _, _, r in ranges)
        return Conflict(
            rule=rule,
            enforcement=rule.enforcement,
            message=f"Outside working hours ({spans})",
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

    def _check_max_per_week(
        self,
        rule: AvailabilityRule,
        user_id: str,
        proposed_start: datetime,
    ) -> Conflict | None:
        """Same count as the daily cap, over the Monday-to-Sunday week.

        The week the proposed time falls in, in the clinician's own frame —
        a cap on how many sessions a week holds is about their week, not a
        rolling seven days ending wherever the booking happens to land.
        """
        week_start = datetime.combine(
            proposed_start.date() - timedelta(days=proposed_start.weekday()),
            time(0),
            tzinfo=proposed_start.tzinfo,
        )
        week_end = week_start + timedelta(days=7)
        existing = self._appt_repo.list_by_range(user_id, week_start, week_end)
        active = [a for a in existing if a.status != "cancelled"]
        max_count = rule.params["max"]
        if len(active) >= max_count:
            return Conflict(
                rule=rule,
                enforcement=rule.enforcement,
                message=(
                    f"Maximum {max_count} appointments per week reached ({len(active)} existing)"
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

    def _day_occupancy(self, user_id: str, day: LocalDay) -> set[int]:
        """The minutes of ``day`` appointments and busy time hold, without buffers.

        What a floating block counts as taken (see ``floating_block``).
        """
        active = [
            a
            for a in self._appt_repo.list_by_range(user_id, day.start, day.end)
            if a.status != "cancelled"
        ]
        occupied = _appointments_to_blocked_minutes(active, day)
        if self._busy_source is not None:
            busy = self._busy_source.busy_between(user_id, day.start, day.end)
            occupied |= _busy_to_blocked_minutes(busy, day)
        return occupied

    def _check_floating_block(
        self,
        rule: AvailabilityRule,
        user_id: str,
        proposed_start: datetime,
        proposed_end: datetime,
    ) -> Conflict | None:
        """Whether the proposed time would leave the block no room that day.

        The message names the block, so the clinician booking directly sees
        what they would be giving up before deciding to go ahead. Every path
        that books without that decision (no override) is refused by a HARD
        block, which is the default.
        """
        on = proposed_start.date()
        if not floating_block.applies_on(rule, on) or floating_block.is_skipped(rule, on):
            return None
        day = LocalDay.of(on, proposed_start.tzinfo or UTC)
        occupied = self._day_occupancy(user_id, day)
        span = day.covered(proposed_start, proposed_end)
        if not floating_block.breaks_block(rule, day, occupied, span):
            return None
        label = rule.params["label"]
        pinned = floating_block.pinned_span(rule, day)
        if pinned is not None:
            message = f"Overlaps {label} ({day.hhmm(pinned[0])}-{day.hhmm(pinned[1])})"
        else:
            message = (
                f"Leaves no time for {label} between "
                f"{rule.params['window_start']} and {rule.params['window_end']}"
            )
        return Conflict(rule=rule, enforcement=rule.enforcement, message=message)

    def floating_blocks(
        self, user_id: str, date_str: str, *, tz: tzinfo = UTC
    ) -> list[ResolvedFloatingBlock]:
        """Where each of the user's floating blocks sits on ``date_str``.

        Read-only: the position is derived from what the day holds, never
        stored, so it moves as the day fills and every reader sees the same
        answer. ``date_str`` is a local date in ``tz``.
        """
        day = LocalDay.of(date.fromisoformat(date_str), tz)
        rules = [
            r
            for r in self._rule_repo.list_by_user(user_id)
            if r.rule_type == RuleType.FLOATING_BLOCK
        ]
        if not rules:
            return []
        occupied = self._day_occupancy(user_id, day)
        resolved: list[ResolvedFloatingBlock] = []
        for block in floating_block.day_blocks(rules, day, occupied):
            params = block.rule.params
            resolved.append(
                ResolvedFloatingBlock(
                    rule_id=block.rule.id,
                    label=block.label,
                    date=day.day,
                    state=block.state.value,
                    duration_minutes=int(params["duration_minutes"]),
                    window_start=params["window_start"],
                    window_end=params["window_end"],
                    start=day.instant(block.start) if block.start is not None else None,
                    end=day.instant(block.end) if block.end is not None else None,
                )
            )
        return resolved

    # --- Free slots helpers ---

    def _get_working_hours(
        self,
        rules: list[AvailabilityRule],
        day: LocalDay,
        appointment_type_id: str | None = None,
    ) -> list[tuple[int, int]]:
        """Get working hour ranges (in ``day``'s axis minutes) for a date and type.

        A type-scoped window NARROWS the practice's own hours rather than
        adding to them — see :meth:`get_free_slots` for why the intersection
        is the defensible reading. With no type-scoped window for the day,
        the practice's hours stand as they always have.
        """
        day_of_week = day.day.weekday()
        practice: list[tuple[int, int]] = []
        scoped: list[tuple[int, int]] = []
        for rule in rules:
            if (
                rule.rule_type != RuleType.WORKING_HOURS
                or rule.params.get("day_of_week") != day_of_week
            ):
                continue
            span = day.span(rule.params["start"], rule.params["end"])
            if rule.appointment_type_id is None:
                practice.append(span)
            elif rule.appointment_type_id == appointment_type_id:
                scoped.append(span)
        if not scoped:
            return sorted(practice)
        return _intersect_ranges(practice, scoped)

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

    def _get_blocked_minutes(self, rules: list[AvailabilityRule], day: LocalDay) -> set[int]:
        """Get blocked minutes on ``day`` from block_time_range rules."""
        blocked: set[int] = set()
        for rule in rules:
            if rule.rule_type == RuleType.BLOCK_TIME_RANGE:
                blocked.update(range(*day.span(rule.params["start"], rule.params["end"])))
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
        """Get the session_defaults rule in force, if any.

        ``rules`` is already narrowed to what applies, so a type-scoped rule
        here is one scoped to the type being listed — and it wins over the
        practice-wide default, which is the whole point of setting it. Ties
        within a scope fall to the first by created_at, as before.
        """
        defaults = [r for r in rules if r.rule_type == RuleType.SESSION_DEFAULTS]
        scoped = [r for r in defaults if r.appointment_type_id is not None]
        chosen = scoped or defaults
        return chosen[0] if chosen else None

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

    def _claimed_minutes(
        self,
        rules: list[AvailabilityRule],
        day: LocalDay,
        appointment_type_id: str | None,
    ) -> tuple[set[int], set[int]]:
        """Minutes another type has claimed exclusively, split hard and soft.

        Every exclusive window belonging to a type OTHER than the one being
        listed subtracts, which is what makes the claim mutual: two types
        whose windows overlap each subtract from the other and neither is
        offered the overlap, whatever order the rules were written in.

        A listing with no type named loses all of them — it cannot promise
        the slot will be used by whoever claimed it.
        """
        day_of_week = day.day.weekday()
        hard: set[int] = set()
        soft: set[int] = set()
        for rule in rules:
            if not _is_exclusive_window(rule) or rule.appointment_type_id == appointment_type_id:
                continue
            if rule.params.get("day_of_week") != day_of_week:
                continue
            claimed = soft if rule.enforcement == EnforcementLevel.SOFT else hard
            claimed |= _window_minutes(rule, day)
        return hard, soft

    def _day_cap_state(
        self,
        rules: list[AvailabilityRule],
        active: list[Appointment],
        appointment_type_id: str | None,
    ) -> _DayCap:
        """Decide what the day's max_per_day rules leave open.

        The practice-wide caps and the type-scoped caps are two separate
        questions asked over two different populations — every active
        appointment, and only this type's — and both are answered. A cap
        that only counts intakes never excuses an intake from the
        practice's own day limit.
        """
        groups: list[tuple[list[AvailabilityRule], int]] = [
            ([r for r in rules if r.appointment_type_id is None], len(active))
        ]
        if appointment_type_id is not None:
            groups.append(
                (
                    [r for r in rules if r.appointment_type_id == appointment_type_id],
                    sum(1 for a in active if a.appointment_type_id == appointment_type_id),
                )
            )

        state = _DayCap.OPEN
        for group, booked in groups:
            cap = self._get_max_per_day(group)
            if cap is None:
                continue
            limit, enforcement = cap
            if booked < limit:
                continue
            if enforcement != EnforcementLevel.SOFT:
                return _DayCap.CLOSED
            state = _DayCap.OVER
        return state

    def exclusivity_warnings(self, rule: AvailabilityRule) -> list[str]:
        """What ``rule`` would take away from the practice's other types.

        Called when a rule is written, because an exclusive window is the one
        rule that can leave a practice with a calendar nothing else fits in,
        and learning that from an empty slot list a week later is no way to
        learn it. These are warnings, not refusals: a practice that means to
        give a whole day to intakes is entitled to.
        """
        if not _is_exclusive_window(rule):
            return []
        try:
            day_of_week = rule.params["day_of_week"]
            claimed = _window_minutes(rule)
        except (KeyError, TypeError, ValueError):
            # Same stance the rest of the engine takes on unvalidated params:
            # a rule we cannot read is a rule we cannot warn about.
            return []

        others = [r for r in self._rule_repo.list_by_user(rule.user_id) if r.id != rule.id]
        same_day = [r for r in others if r.params.get("day_of_week") == day_of_week]
        day = calendar.day_name[day_of_week]
        warnings: list[str] = []

        rival_claims = [
            r
            for r in same_day
            if _is_exclusive_window(r) and r.appointment_type_id != rule.appointment_type_id
        ]
        if any(claimed & _window_minutes(r) for r in rival_claims):
            warnings.append(
                f"This window overlaps one another appointment type already claims on "
                f"{day}. Neither type is offered the overlapping minutes."
            )

        practice_hours: set[int] = set()
        for r in same_day:
            if r.rule_type == RuleType.WORKING_HOURS and r.appointment_type_id is None:
                practice_hours |= _window_minutes(r)
        all_claims = claimed.union(*(_window_minutes(r) for r in rival_claims))
        if practice_hours and not practice_hours - all_claims:
            warnings.append(f"This leaves no {day} availability for any other appointment type.")
        return warnings

    def _get_max_per_day(self, rules: list[AvailabilityRule]) -> tuple[int, str] | None:
        """Get the most restrictive max_per_day cap and its enforcement level.

        The smallest cap wins, as before. But if any MAX_PER_DAY rule is
        HARD, HARD wins the day-closing decision even when a lower SOFT cap
        is the one that set the number — a hard limit isn't softened by a
        softer rule also being present.
        """
        result: int | None = None
        enforcement: str | None = None
        hard_present = False
        for rule in rules:
            if rule.rule_type == RuleType.MAX_PER_DAY:
                max_val = rule.params["max"]
                if result is None or max_val < result:
                    result = max_val
                    enforcement = rule.enforcement
                if rule.enforcement == EnforcementLevel.HARD:
                    hard_present = True
        if result is None or enforcement is None:
            return None
        if hard_present:
            enforcement = EnforcementLevel.HARD
        return result, enforcement


def _appointments_to_blocked_minutes(
    appointments: list[Appointment],
    day: LocalDay,
    buffer_before: int = 0,
    buffer_after: int = 0,
) -> set[int]:
    """The minutes of ``day`` existing appointments hold, with the buffers round them."""
    blocked: set[int] = set()
    for appt in appointments:
        blocked |= day.covered(
            _local(appt.start_at, day.tz), _local(appt.end_at, day.tz), buffer_before, buffer_after
        )
    return blocked


def _busy_to_blocked_minutes(
    windows: list[BusyInterval],
    day: LocalDay,
    buffer_before: int = 0,
    buffer_after: int = 0,
) -> set[int]:
    """The minutes of ``day`` that busy windows cover.

    Read on the same axis appointments are, so a busy window and an
    appointment at the same instant block the same minutes. Partial minutes
    round outward: a window ending at 10:00:30 still holds 10:00.

    The clinician's buffers go round each window exactly as they go round an
    appointment (see :func:`_appointments_to_blocked_minutes`).
    A buffer is the gap kept around a commitment, whatever the commitment is
    — an outside session is a session, and a meeting on the calendar still
    needs the time before the next client.
    """
    blocked: set[int] = set()
    for window in windows:
        blocked |= day.covered(
            _local(window.start, day.tz), _local(window.end, day.tz), buffer_before, buffer_after
        )
    return blocked
