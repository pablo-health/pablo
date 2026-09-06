# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for the unbilled-sessions queue.

One row per finalized session with no succeeded charge — see
``app.routes.billing_queue`` for how "unbilled" is derived.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

# Runtime import: Pydantic resolves these annotations at runtime for
# validation, so they cannot live in a TYPE_CHECKING block.
from .claims import ClaimState, FrequencyCode  # noqa: TC001


class UnbilledClaimSummary(BaseModel):
    """The newest claim already filed for the row's visit, if there is one.

    Enough for the row to say where the claim stands and link to it; the
    claim itself is read from the claims routes.
    """

    id: str
    control_number: str
    state: ClaimState
    frequency_code: FrequencyCode


class UnbilledSessionItem(BaseModel):
    """One row of the queue.

    ``amount_cents`` is the resolved rate a charge from this session would
    use today (the client's own rate, else the appointment type's default) —
    the same resolution the charge action itself applies. It is ``None``
    when neither is set, matching :class:`ChargeAmountResponse`.

    ``has_coverage`` says the client has active coverage on file, which is
    what makes "File claim" an option beside "Charge card". ``claim`` is
    the newest claim on the visit, so a row whose claim is on its way is
    shown as such rather than offered for filing again.
    """

    session_id: str
    patient_id: str
    patient_name: str
    session_date: datetime
    amount_cents: int | None
    currency: str
    appointment_id: str | None = None
    has_coverage: bool = False
    claim: UnbilledClaimSummary | None = None


class UnbilledQueueResponse(BaseModel):
    """The unbilled queue, newest session first."""

    items: list[UnbilledSessionItem]
