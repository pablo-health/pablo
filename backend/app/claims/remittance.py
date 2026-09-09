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

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .clearinghouse import ClearinghouseError
from .receipts import record
from .transitions import advance, next_state

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from ..models.claims import Claim
    from ..models.claims_timeline import ClaimTimeline
    from .receipts import ClaimPipeline
    from .sdk_timeline import ClaimTimelineSource

logger = logging.getLogger(__name__)

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
    #: The latest adjudicating entry this reading is based on. Keys the
    #: receipt, so re-reading a timeline that has not changed posts nothing
    #: a second time, while a reversal arriving later is a new entry and
    #: does post.
    source_id: str


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
    latest = max(adjudications, key=lambda payment: payment.processed_at)
    adjudicated_at = latest.processed_at
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
            source_id=latest.id,
        )

    event: RemittanceEvent = "pay" if paid_cents >= charged_cents > 0 else "pay_partial"
    return RemittancePosting(
        event=event,
        paid_cents=paid_cents,
        patient_responsibility_cents=timeline.patient_responsibility_cents,
        trace_number=trace_number,
        adjudicated_at=adjudicated_at,
        source_id=latest.id,
    )


def apply_posting(
    pipeline: ClaimPipeline, claim: Claim, posting: RemittancePosting
) -> tuple[Claim, bool]:
    """Write a remittance onto a claim; the claim after, and whether it moved.

    Idempotent on the vendor's own entry id. Re-reading a timeline that has
    not changed writes nothing a second time — which matters because reading
    is cheap and will happen on a schedule, while double-posting money is a
    number a practice would have to unpick by hand.

    A claim already in a terminal state is left alone rather than raising:
    a payer that sends a second remittance for a claim we have already
    closed is telling us something, but it is not a reason to fail the whole
    polling pass.
    """
    # Scoped to the claim rather than the vendor's entry id alone. The
    # uniqueness constraint behind this spans the whole table, so an id the
    # vendor ever reused across claims would silently skip a real payment
    # on the second one. Namespacing costs nothing and removes the
    # assumption that the vendor's ids are globally unique forever.
    event_key = f"{claim.id}:{posting.source_id}"
    if pipeline.receipts.vendor_event_seen(event_key):
        return claim, False
    if next_state(claim.state, posting.event) is None:
        logger.info(
            "remittance_not_applicable claim_id=%s state=%s event=%s",
            claim.id,
            claim.state,
            posting.event,
        )
        return claim, False

    now = pipeline.now()
    moved = advance(claim, posting.event, now=now)
    stored = pipeline.claims.update(
        moved.model_copy(update={"total_paid_cents": posting.paid_cents})
    )
    record(
        pipeline,
        stored,
        "adjudicated",
        detail={
            "disposition": posting.event,
            "paid_cents": posting.paid_cents,
            "patient_responsibility_cents": posting.patient_responsibility_cents,
            "trace_number": posting.trace_number,
        },
        vendor_event_id=event_key,
        occurred_at=posting.adjudicated_at,
        touches_receipt_clock=True,
    )
    logger.info(
        "remittance_posted claim_id=%s event=%s paid_cents=%d",
        stored.id,
        posting.event,
        posting.paid_cents,
    )
    return stored, True


def post_remittances(
    pipeline: ClaimPipeline, timelines: ClaimTimelineSource, claims: Iterable[Claim]
) -> int:
    """Read each claim's timeline and post whatever the payer decided.

    Returns how many claims moved.

    One claim's clearinghouse failing is not the pass failing: the others are
    still worth reading, and a claim that could not be read this time is read
    again on the next pass. A claim the clearinghouse has no id for was never
    filed through it and has no timeline to ask about.
    """
    moved = 0
    for claim in claims:
        if not claim.vendor_claim_id:
            continue
        try:
            timeline = timelines.timeline_for(claim.vendor_claim_id)
        except ClearinghouseError:
            logger.warning("remittance_read_failed claim_id=%s", claim.id)
            continue
        posting = posting_for(timeline, charged_cents=claim.total_charge_cents)
        if posting is None:
            continue
        _, did_move = apply_posting(pipeline, claim, posting)
        moved += int(did_move)
    return moved
