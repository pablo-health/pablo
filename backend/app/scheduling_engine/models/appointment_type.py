# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment type domain model — what an appointment is and when it may be offered."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from ...money import DollarAmount, cents_to_dollars, dollars_to_cents, format_money

Audience = Literal["new", "existing", "both"]
HorizonUnit = Literal["business", "days"]


@dataclass
class AppointmentType:
    """A kind of appointment, with its own length and its own booking window.

    A fifteen-minute consultation and a sixty-minute intake do not want the
    same notice, lead time or horizon, which is why these live per type rather
    than practice-wide.

    ``default_fee_cents`` is the fee absent a per-patient override. Stored in
    minor units; ``None`` means unset, not free — see
    :mod:`app.scheduling_engine.services.rate_resolver`.

    ``min_notice_hours`` is ``None`` when the type defers to the practice
    default. That is a different statement from ``0``, which means this type
    needs no notice at all, so do not collapse them.
    """

    id: str
    user_id: str
    name: str
    default_fee_cents: int | None = None
    duration_minutes: int = 50
    audience: Audience = "existing"
    min_notice_hours: int | None = None
    earliest_offer_business_days: int = 1
    horizon: int = 10
    horizon_unit: HorizonUnit = "business"
    self_bookable: bool = False
    offerable: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None

    _FIELDS = (
        "id",
        "user_id",
        "name",
        "default_fee_cents",
        "duration_minutes",
        "audience",
        "min_notice_hours",
        "earliest_offer_business_days",
        "horizon",
        "horizon_unit",
        "self_bookable",
        "offerable",
        "created_at",
        "updated_at",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppointmentType:
        """Create AppointmentType from dictionary.

        Absent keys fall back to the dataclass defaults, so a row written
        before the scheduling fields existed reads as today's behaviour rather
        than as nulls.
        """
        known = {k: data[k] for k in cls._FIELDS if k in data and data[k] is not None}
        # min_notice_hours is meaningfully nullable, so it bypasses the filter
        # above: "defer to the practice" must survive a round trip.
        if "min_notice_hours" in data:
            known["min_notice_hours"] = data["min_notice_hours"]
        if "default_fee_cents" in data:
            known["default_fee_cents"] = data["default_fee_cents"]
        return cls(**known)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {name: getattr(self, name) for name in self._FIELDS}

    # --- Money, at the human boundary -------------------------------------
    #
    # The column is cents; people type and read dollars. Going through these
    # rather than touching ``default_fee_cents`` directly is what keeps a
    # hundred-fold error out of a fee field — see :mod:`app.money`.

    @property
    def fee_dollars(self) -> Decimal | None:
        """The fee as an exact dollar amount, for an editable field.

        ``None`` means no fee has been set, which is not the same as free.
        """
        return cents_to_dollars(self.default_fee_cents)

    @fee_dollars.setter
    def fee_dollars(self, amount: DollarAmount | None) -> None:
        """Set the fee from a dollar amount someone typed."""
        self.default_fee_cents = dollars_to_cents(amount)

    @property
    def fee_display(self) -> str:
        """The fee as it reads on screen: ``$160``, ``Free``, or blank."""
        return format_money(self.default_fee_cents)

    @property
    def duration_display(self) -> str:
        """The length as it reads on screen: ``50 min``."""
        return f"{self.duration_minutes} min"

    @property
    def summary(self) -> str:
        """The one-line summary a list row shows: ``50 min · $160``.

        Falls back to just the length when no fee is set, rather than showing
        a dangling separator.
        """
        fee = self.fee_display
        return f"{self.duration_display} · {fee}" if fee else self.duration_display
