# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for effective-rate resolution (patient override -> type default -> unset)."""

from __future__ import annotations

from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.services.rate_resolver import resolve_rate_cents

USER_ID = "user-1"


def _appointment_type(default_fee_cents: int | None) -> AppointmentType:
    return AppointmentType(
        id="type-1",
        user_id=USER_ID,
        name="individual",
        default_fee_cents=default_fee_cents,
    )


class TestResolveRateCents:
    def test_patient_override_wins_over_type_default(self) -> None:
        resolved = resolve_rate_cents(15000, _appointment_type(12000))
        assert resolved == 15000

    def test_falls_back_to_type_default_when_no_override(self) -> None:
        resolved = resolve_rate_cents(None, _appointment_type(12000))
        assert resolved == 12000

    def test_unset_when_neither_override_nor_type_default(self) -> None:
        resolved = resolve_rate_cents(None, _appointment_type(None))
        assert resolved is None

    def test_unset_when_no_appointment_type(self) -> None:
        resolved = resolve_rate_cents(None, None)
        assert resolved is None

    def test_unset_is_not_zero(self) -> None:
        """An unresolved rate must stay None -- never silently coerced to 0.

        A blank fee is a visible prompt to fill it in; a 0 looks like a
        deliberate free-session decision.
        """
        resolved = resolve_rate_cents(None, _appointment_type(None))
        assert resolved is None
        assert resolved != 0
