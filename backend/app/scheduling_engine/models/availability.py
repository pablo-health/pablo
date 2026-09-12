# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Availability rule domain model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class RuleType(StrEnum):
    WORKING_HOURS = "working_hours"
    BLOCK_DAY_OF_WEEK = "block_day_of_week"
    BLOCK_TIME_RANGE = "block_time_range"
    MAX_PER_DAY = "max_per_day"
    BUFFER_BEFORE = "buffer_before"
    BUFFER_AFTER = "buffer_after"
    BLOCK_DATE_RANGE = "block_date_range"
    BLOCK_SPECIFIC_DATES = "block_specific_dates"
    SESSION_DEFAULTS = "session_defaults"


class EnforcementLevel(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass
class AvailabilityRule:
    """A rule constraining when appointments can be scheduled.

    params varies by rule_type — see design doc §3.4 for schemas.
    """

    id: str
    user_id: str
    rule_type: str  # RuleType value
    enforcement: str  # EnforcementLevel value
    params: dict[str, Any]
    #: Which appointment type this rule governs, or None for all of them.
    #:
    #: None is the practice-wide rule and the default: "Mondays 9-5" applies
    #: to every kind of appointment, which is what every rule written before
    #: this column meant and still means. Setting it narrows the rule to one
    #: type — "at most two intakes a day" is a MAX_PER_DAY rule with a type,
    #: not a new rule type.
    appointment_type_id: str | None = None
    #: Whether other appointment types may use the window this rule defines.
    #:
    #: True — the default — is today's behaviour: scoping a WORKING_HOURS
    #: window to a type says when that type may be offered and says nothing
    #: about anybody else. False turns the window into a claim: those minutes
    #: are offered to this type and to no other.
    #:
    #: Only meaningful on a type-scoped rule that defines a window. A
    #: practice-wide rule has no other types to exclude, and a rule that only
    #: counts or blocks does not define a window to hand out.
    allow_other_types: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvailabilityRule:
        """Create AvailabilityRule from dictionary."""
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            rule_type=data["rule_type"],
            enforcement=data["enforcement"],
            params=data.get("params", {}),
            appointment_type_id=data.get("appointment_type_id"),
            allow_other_types=data.get("allow_other_types", True),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "rule_type": self.rule_type,
            "enforcement": self.enforcement,
            "params": self.params,
            "appointment_type_id": self.appointment_type_id,
            "allow_other_types": self.allow_other_types,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
