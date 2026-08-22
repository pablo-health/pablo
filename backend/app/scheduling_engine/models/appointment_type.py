# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment type domain model — practice-level default fees."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class AppointmentType:
    """A named appointment type with an optional practice-level default fee.

    ``default_fee_cents`` is the fee a clinician charges for this type of
    session (e.g. "individual", "intake") unless a patient carries a rate
    override. Stored as integer minor units (cents); ``None`` means unset,
    not free — see :mod:`app.scheduling_engine.services.rate_resolver`.
    """

    id: str
    user_id: str
    name: str
    default_fee_cents: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppointmentType:
        """Create AppointmentType from dictionary."""
        return cls(
            id=data["id"],
            user_id=data["user_id"],
            name=data["name"],
            default_fee_cents=data.get("default_fee_cents"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "default_fee_cents": self.default_fee_cents,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
