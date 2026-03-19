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
