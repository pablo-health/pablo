# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for the unbilled-sessions queue.

One row per finalized session with no succeeded charge — see
``app.routes.billing_queue`` for how "unbilled" is derived.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class UnbilledSessionItem(BaseModel):
    """One row of the queue.

    ``amount_cents`` is the resolved rate a charge from this session would
    use today (the client's own rate, else the appointment type's default) —
    the same resolution the charge action itself applies. It is ``None``
    when neither is set, matching :class:`ChargeAmountResponse`.
    """

    session_id: str
    patient_id: str
    patient_name: str
    session_date: datetime
    amount_cents: int | None
    currency: str


class UnbilledQueueResponse(BaseModel):
    """The unbilled queue, newest session first."""

    items: list[UnbilledSessionItem]
