# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Conflict and time slot models for availability checking."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .availability import AvailabilityRule


@dataclass
class TimeSlot:
    """A time window with start and end."""

    start: str  # ISO 8601 UTC
    end: str  # ISO 8601 UTC


@dataclass
class Conflict:
    """A scheduling conflict detected by the availability engine."""

    rule: AvailabilityRule
    enforcement: str  # EnforcementLevel value
    message: str
    suggested_alternatives: list[TimeSlot] = field(default_factory=list)


@dataclass
class ConflictCheckResult:
    """Result of checking a proposed time against a user's availability rules.

    ``configured`` is False when the user has no availability rules at all —
    a distinct state from "checked and found nothing wrong". Zero rules means
    nothing has been asserted about availability, so ``conflicts`` is always
    empty in that case; callers that need to tell "not configured" apart from
    "configured and clear" should check ``configured`` rather than treating
    an empty ``conflicts`` list as an answer either way.
    """

    configured: bool
    conflicts: list[Conflict]


@dataclass
class FreeSlotsResult:
    """Result of computing free slots for a date.

    ``configured`` is False when the user has no availability rules at all,
    which is why ``slots`` is empty — not because the day is fully booked.
    Callers must check ``configured`` before treating an empty ``slots`` list
    as "no openings"; otherwise "not set up yet" renders as "fully booked".
    """

    configured: bool
    slots: list[TimeSlot]
    duration_minutes: int = 0
