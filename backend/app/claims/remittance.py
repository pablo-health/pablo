# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Deciding what a payer's remittance did to a claim.

A claim sits at ``payer_accepted`` until the payer says what it did with it.
This reads a claim's timeline and works out which of the three answers came
back — paid in full, adjudicated for less than the charge, or denied — along
with the amounts that go on the claim.

Pure: no I/O, no logging, no database. The posting itself, and the state
transition it drives, are the caller's.

Two readings are worth being explicit about, because a tracker that gets
either one wrong tells a practice something false about its own money.

**Adjudicated for zero is not a denial.** A payer that applies the whole
charge to the client's deductible has processed the claim and paid nothing;
the money is now owed by the client rather than refused. That is reported as
partial, not denied, so the balance lands on the client and the claim is not
filed away as a loss to appeal.

**Nothing to post is not the same as denied.** A timeline carrying only a
predetermination, or a claim forwarded to another payer, has no adjudication
in it at all. The claim stays where it is and waits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from datetime import datetime

    from ..models.claims_timeline import ClaimTimeline

#: The state-machine events a remittance can drive. See
#: ``app.claims.transitions``; every one of these is legal from both
#: ``payer_accepted`` and ``stalled``.
RemittanceEvent = Literal["pay", "pay_partial", "deny"]


@dataclass(frozen=True, slots=True)
class RemittancePosting:
    """What a remittance says to write on a claim."""

    event: RemittanceEvent
    paid_cents: int
    patient_responsibility_cents: int
    #: The check number or EFT trace of the most recent entry that moved
    #: money — what ties this claim to funds in the practice's account.
    trace_number: str | None
    adjudicated_at: datetime


def posting_for(timeline: ClaimTimeline, *, charged_cents: int) -> RemittancePosting | None:
    """What to write on a claim whose timeline reads like this.

    ``None`` when the payer has not adjudicated: no remittance yet, or only
    entries that carry amounts without moving money.
    """
    adjudications = [
        payment
        for payment in timeline.payments
        if payment.disposition in ("paid", "denied", "reversed")
    ]
    if not adjudications:
        return None

    paid_cents = timeline.paid_cents
    adjudicated_at = max(payment.processed_at for payment in adjudications)
    money = [payment for payment in adjudications if payment.is_money]
    trace_number = next(
        (
            payment.trace_number
            for payment in sorted(money, key=lambda p: p.processed_at, reverse=True)
            if payment.trace_number
        ),
        None,
    )

    if paid_cents <= 0 and not money:
        # Every adjudication was a denial and nothing was ever paid.
        return RemittancePosting(
            event="deny",
            paid_cents=0,
            patient_responsibility_cents=timeline.patient_responsibility_cents,
            trace_number=None,
            adjudicated_at=adjudicated_at,
        )

    event: RemittanceEvent = "pay" if paid_cents >= charged_cents > 0 else "pay_partial"
    return RemittancePosting(
        event=event,
        paid_cents=paid_cents,
        patient_responsibility_cents=timeline.patient_responsibility_cents,
        trace_number=trace_number,
        adjudicated_at=adjudicated_at,
    )
