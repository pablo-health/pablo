# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Effective-rate resolution for a (patient, appointment type) pair.

The only place the patient-override -> type-default -> unset precedence is
expressed. Anything that renders money (statements, receipts) must call
:func:`resolve_rate_cents` rather than re-deriving the order itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.appointment_type import AppointmentType


def resolve_rate_cents(
    patient_rate_cents: int | None,
    appointment_type: AppointmentType | None,
) -> int | None:
    """Resolve the effective rate in integer minor units (cents).

    Order: patient override -> appointment-type default -> unset (``None``).
    ``None`` is a legitimate result — callers must not coerce it to 0.
    """
    if patient_rate_cents is not None:
        return patient_rate_cents
    if appointment_type is not None:
        return appointment_type.default_fee_cents
    return None
