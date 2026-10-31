# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Floating blocks: time kept free somewhere inside a window.

A ``floating_block`` rule says "on these days, keep ``duration_minutes``
free somewhere between ``window_start`` and ``window_end``" — an hour for
lunch somewhere between 11:30 and 2, wherever the day leaves room. A
``block_time_range`` pins the time; this one lets it move.

Three answers come from the same arithmetic, kept here so the listing, the
booking-side check and the calendar can never disagree:

* **Does a booking leave room?** A slot is offered, and a booking made
  without an override is accepted, only if some free stretch of the block's
  length is still left inside the window afterwards.
* **Where is it today?** The block resolves to the EARLIEST free stretch of
  its length inside the window. Deterministic, so the calendar, the
  clinician and anything else reading the day see the same position.
* **Per-date overrides.** ``exceptions`` maps a date to a pin (``start``:
  the block sits at that time that day, like a fixed block) or a skip (no
  block that day). Past dates are pruned whenever an exception is written,
  so the map does not grow forever.

"Free" means no appointment and no busy window from the clinician's
calendar. Buffers are not counted: a buffer is the gap kept between two
sessions, and lunch can sit in it. Working hours are not counted either:
time outside working hours is still the clinician's own.

All minute arithmetic is on a :class:`~.local_day.LocalDay`'s axis — real
minutes since the date began in the clinician's zone, the frame the rest of
the engine uses — and every ``HH:MM`` a rule names reaches it through
:meth:`~.local_day.LocalDay.minute_of`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from ..models.availability import RuleType

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..models.availability import AvailabilityRule
    from .local_day import LocalDay


class BlockState(StrEnum):
    """What a floating block is doing on one date."""

    #: Settled into the earliest free stretch of the window.
    FLOATING = "floating"
    #: Pinned to a set start for this date.
    PINNED = "pinned"
    #: Skipped for this date: nothing is protected.
    SKIPPED = "skipped"
    #: No free stretch of its length is left. Only a booking made directly,
    #: past the warning, can get here; nothing automated ever does.
    UNPLACED = "unplaced"


@dataclass(frozen=True)
class DayBlock:
    """One floating block on one date, in the day's axis minutes.

    ``start`` and ``end`` are None when the block is skipped or unplaced.
    """

    rule: AvailabilityRule
    state: BlockState
    start: int | None = None
    end: int | None = None

    @property
    def label(self) -> str:
        return str(self.rule.params.get("label", ""))


def window_of(rule: AvailabilityRule, day: LocalDay) -> tuple[int, int]:
    """The rule's window on ``day`` as half-open axis minutes."""
    return day.span(rule.params["window_start"], rule.params["window_end"])


def duration_of(rule: AvailabilityRule) -> int:
    return int(rule.params["duration_minutes"])


def applies_on(rule: AvailabilityRule, day: date) -> bool:
    """Whether the rule is a floating block that covers ``day``'s weekday."""
    return rule.rule_type == RuleType.FLOATING_BLOCK and day.weekday() in rule.params.get(
        "days_of_week", []
    )


def exception_for(rule: AvailabilityRule, day: date) -> dict[str, Any] | None:
    exceptions = rule.params.get("exceptions") or {}
    found = exceptions.get(day.isoformat())
    return found if isinstance(found, dict) else None


def pinned_span(rule: AvailabilityRule, day: LocalDay) -> tuple[int, int] | None:
    """The block's fixed minutes on ``day`` when it is pinned there, else None."""
    exception = exception_for(rule, day.day)
    if not exception or not exception.get("start"):
        return None
    start = day.minute_of(exception["start"])
    return start, min(start + duration_of(rule), day.minutes)


def is_skipped(rule: AvailabilityRule, day: date) -> bool:
    exception = exception_for(rule, day)
    return bool(exception and exception.get("skip"))


def earliest_stretch(occupied: set[int], window: tuple[int, int], duration: int) -> int | None:
    """The first minute of the earliest free run of ``duration`` inside ``window``."""
    run_start: int | None = None
    for minute in range(window[0], window[1]):
        if minute in occupied:
            run_start = None
            continue
        if run_start is None:
            run_start = minute
        if minute - run_start + 1 >= duration:
            return run_start
    return None


def leaves_room(occupied: set[int], window: tuple[int, int], duration: int) -> bool:
    return earliest_stretch(occupied, window, duration) is not None


def day_blocks(
    rules: Iterable[AvailabilityRule], day: LocalDay, occupied: set[int]
) -> list[DayBlock]:
    """Where each floating block that covers ``day`` sits, given what is booked.

    A rule whose params cannot be read is left out, the stance the engine
    takes on every malformed rule: it must not take the rest of the day down.
    """
    blocks: list[DayBlock] = []
    for rule in rules:
        try:
            if not applies_on(rule, day.day):
                continue
            if is_skipped(rule, day.day):
                blocks.append(DayBlock(rule, BlockState.SKIPPED))
                continue
            pinned = pinned_span(rule, day)
            if pinned is not None:
                blocks.append(DayBlock(rule, BlockState.PINNED, *pinned))
                continue
            duration = duration_of(rule)
            start = earliest_stretch(occupied, window_of(rule, day), duration)
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        if start is None:
            blocks.append(DayBlock(rule, BlockState.UNPLACED))
        else:
            blocks.append(DayBlock(rule, BlockState.FLOATING, start, start + duration))
    return blocks


def breaks_block(rule: AvailabilityRule, day: LocalDay, occupied: set[int], span: set[int]) -> bool:
    """Whether also booking ``span`` on ``day`` would break ``rule``'s block.

    ``occupied`` is what the day already holds. A pinned block is broken by
    any overlap with its fixed minutes; a floating one only when no free
    stretch of its length would be left inside the window. A skipped day,
    or a day the rule does not cover, is never broken.
    """
    if not applies_on(rule, day.day) or is_skipped(rule, day.day):
        return False
    pinned = pinned_span(rule, day)
    if pinned is not None:
        return bool(span & set(range(*pinned)))
    window = window_of(rule, day)
    duration = duration_of(rule)
    if not leaves_room(occupied, window, duration):
        # Already broken by what is booked (a direct booking past the
        # warning). Only a booking that reaches into the window makes it
        # worse; one elsewhere in the day has nothing to do with it.
        return bool(span & set(range(*window)))
    return not leaves_room(occupied | span, window, duration)


def with_exception(
    params: dict[str, Any], day: date, exception: dict[str, Any] | None, *, today: date
) -> dict[str, Any]:
    """``params`` with ``day``'s exception set (or cleared, for None).

    Exceptions for dates before ``today`` are dropped on the way, so the map
    only ever holds what is still ahead. The result still goes through the
    params validation before it is stored.
    """
    kept = {
        key: value
        for key, value in (params.get("exceptions") or {}).items()
        if key >= today.isoformat() and key != day.isoformat()
    }
    if exception is not None:
        kept[day.isoformat()] = exception
    updated = {key: value for key, value in params.items() if key != "exceptions"}
    if kept:
        updated["exceptions"] = dict(sorted(kept.items()))
    return updated


__all__ = [
    "BlockState",
    "DayBlock",
    "applies_on",
    "breaks_block",
    "day_blocks",
    "duration_of",
    "earliest_stretch",
    "exception_for",
    "is_skipped",
    "leaves_room",
    "pinned_span",
    "window_of",
    "with_exception",
]
