# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Availability rule domain model."""

from __future__ import annotations

from dataclasses import dataclass
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
    created_at: str = ""  # ISO 8601 UTC
    updated_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AvailabilityRule:
        """Create AvailabilityRule from Firestore document."""
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            rule_type=data["rule_type"],
            enforcement=data["enforcement"],
            params=data.get("params", {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Firestore storage."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "rule_type": self.rule_type,
            "enforcement": self.enforcement,
            "params": self.params,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
