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
from typing import TYPE_CHECKING, Literal, Protocol

from ..db.models import DEFAULT_CHARGE_CURRENCY
from . import holds
from .clearinghouse import ClearinghouseError
from .receipts import announce, record
from .remittance_lines import DENIED, applied_to, disagreement_in
from .transitions import advance, next_state

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from ..models.claims import Claim
    from ..models.claims_responses import RemittanceClaim
    from ..models.claims_timeline import ClaimTimeline
    from ..repositories.patient_payment import PatientPaymentRepository
    from .receipts import ClaimPipeline
    from .sdk_timeline import ClaimTimelineSource

logger = logging.getLogger(__name__)

#: The state-machine events a remittance can drive. See
#: ``app.claims.transitions``; every one of these is legal from both
#: ``payer_accepted`` and ``stalled``.
RemittanceEvent = Literal["pay", "pay_partial", "deny"]


class RemittanceDetailSource(Protocol):
    """Where a claim's service-line adjudication comes from.

    Separate from the timeline because it comes from somewhere else and can
    be absent: the vendor's claim API reports payment at claim level only, so
    the per-line breakdown has to be read out of the 835 itself.
    """

    def detail_for(self, control_number: str) -> RemittanceClaim | None:
        """The 835's entry for this claim, or ``None`` if it has not arrived."""
        ...


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


def posting_from_detail(
    detail: RemittanceClaim, *, adjudicated_at: datetime, source_id: str
) -> RemittancePosting:
    """A posting straight from one claim's 835 detail, for the webhook path.

    ``posting_for`` reads a claim's whole timeline because the periodic pass
    has nothing else; the webhook already has the remittance that just
    arrived, so this classifies off the same CLP02 the line-by-line reading
    in ``app.claims.remittance_lines`` uses rather than waiting to reconcile
    against a separately-fetched timeline.
    """
    event: RemittanceEvent = (
        "deny"
        if detail.claim_status_code == DENIED
        else "pay"
        if detail.paid_cents >= detail.total_charge_cents > 0
        else "pay_partial"
    )
    return RemittancePosting(
        event=event,
        paid_cents=detail.paid_cents,
        patient_responsibility_cents=detail.patient_responsibility_cents,
        trace_number=None,
        adjudicated_at=adjudicated_at,
        source_id=source_id,
    )


def apply_remittance(
    pipeline: ClaimPipeline,
    detail: RemittanceClaim,
    *,
    transaction_id: str,
    occurred_at: datetime | None,
) -> tuple[Literal["moved", "duplicate", "not_applicable", "unmatched"], Claim | None]:
    """Post one claim's 835 detail the moment the webhook delivers it.

    The periodic pipeline reaches the same claim later through the vendor's
    claim-timeline API, on its own schedule; this is the fast path, reading
    the remittance itself instead of waiting. Both go through
    :func:`apply_posting`, which is idempotent on the vendor entry id — here,
    the transaction plus the claim it names — so whichever runs second
    writes nothing.
    """
    claim = pipeline.claims.get_by_control_number(detail.patient_control_number)
    if claim is None:
        return "unmatched", None
    posting = posting_from_detail(
        detail,
        adjudicated_at=occurred_at or pipeline.now(),
        source_id=f"835:{transaction_id}:{detail.patient_control_number}",
    )
    _, moved = apply_posting(pipeline, claim, posting, detail=detail)
    if moved:
        return "moved", claim
    # apply_posting declines for two unrelated reasons and used to report
    # both as "duplicate": the posting was already applied, or the claim is
    # in a state this event has no transition for. The second is not a
    # duplicate of anything — it is a payment we could not book — and calling
    # it one hides it behind the outcome that means "nothing to do here".
    if next_state(claim.state, posting.event) is None:
        return "not_applicable", claim
    return "duplicate", claim


#: The ledger row kind that carries what a payer said a client owes.
#: Defined in :mod:`app.claims.holds` — the settle path writes the same row
#: this path withholds — and re-exported here, where callers already look
#: for it.
PATIENT_RESPONSIBILITY_KIND = holds.PATIENT_RESPONSIBILITY_KIND


def patient_responsibility_billed(charges: PatientPaymentRepository, claim: Claim) -> int:
    """How much of this claim has already been put on the client's ledger.

    Read from the ledger rather than tracked on the claim, because the ledger
    is where the answer actually lives — a row written by an earlier
    remittance, or corrected by hand afterwards, both count. Summing a stored
    figure instead would drift from the thing it is meant to describe.
    """
    return sum(
        row.amount_cents
        for row in charges.list_charges(claim.patient_id)
        if row.claim_id == claim.id and row.kind == PATIENT_RESPONSIBILITY_KIND
    )


def apply_posting(
    pipeline: ClaimPipeline,
    claim: Claim,
    posting: RemittancePosting,
    *,
    charges: PatientPaymentRepository | None = None,
    detail: RemittanceClaim | None = None,
) -> tuple[Claim, bool]:
    """Write a remittance onto a claim; the claim after, and whether it moved.

    Idempotent on the vendor's own entry id. Re-reading a timeline that has
    not changed writes nothing a second time — which matters because reading
    is cheap and will happen on a schedule, while double-posting money is a
    number a practice would have to unpick by hand.

    An already-adjudicated claim can be adjudicated again — a second payer,
    or a payer taking money back — and the revised answer is written the same
    way the first was. A claim in a state the event has no transition for is
    left alone rather than raising: a remittance for a claim that never
    reached a payer is telling us something, but it is not a reason to fail
    the whole polling pass.
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
    updates: dict[str, object] = {"total_paid_cents": posting.paid_cents}
    if detail is not None:
        # The claim total says a claim was paid $180 of $300; only the lines
        # say whether that was two sessions with a deductible applied or one
        # paid and one denied. Those are different conversations, so post the
        # detail whenever the 835 was available to read.
        updates["lines"] = applied_to(moved.lines, detail)
    stored = pipeline.claims.update(moved.model_copy(update=updates))
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
    # Does the remittance account for its own numbers? Asked whenever the
    # 835 was available to read, and asked BEFORE and INDEPENDENTLY of
    # whether this caller writes to the client's ledger — the webhook path
    # passes no ``charges`` but is usually the first to see the document,
    # and a hold nobody raised because the wrong path got there first is a
    # hold that never happens.
    disagreement = None
    if detail is not None:
        disagreement = disagreement_in(detail)
        if disagreement is not None:
            stored_hold = holds.record(
                pipeline.holds,
                holds.hold_for(
                    stored,
                    detail,
                    disagreement,
                    patient_responsibility_cents=posting.patient_responsibility_cents,
                    posting_key=event_key,
                    now=now,
                ),
            )
            if stored_hold is not None:
                # A hold nobody meets is a client whose balance quietly
                # stopped being billed, so it goes in front of the
                # clinician who owns the claim as work — through the same
                # reminder surface a rejection or a denial uses, rather
                # than a notification channel of its own.
                #
                # Only on a hold that was actually written. Announcing a
                # duplicate would put a second reminder in front of
                # somebody for a disagreement they have already been told
                # about.
                announce(pipeline, stored, "remittance_held")

    if charges is not None:
        # What the payer says the client owes becomes a row on the client's
        # own ledger. Without this the money stops at the claim: the practice
        # can see that a payer paid $80 of $150 and the client is never told
        # about the $20.
        #
        # What is written is the DIFFERENCE from what this claim has already
        # billed, not the amount itself, because a remittance states the
        # balance rather than adding to it. A claim with secondary coverage
        # gets a remittance from each payer, and each states what the client
        # owes after that payer adjudicated — so adding them up bills one
        # session twice. A secondary that pays off the primary's coinsurance
        # produces a negative difference here, which is a credit and is
        # exactly right.
        #
        # Written inside the same branch that records the receipt, so the
        # receipt's idempotency covers the ordinary single-payer case too.
        #
        # Not written at all when the remittance contradicts itself. That is
        # the one thing a hold changes: the payer's payment above still
        # posted, the claim still moved, the receipt still says adjudicated
        # — but a real person is not billed a figure this engine's own
        # arithmetic cannot corroborate. A practice settles it themselves
        # (``app.claims.holds``); nothing here ever decides for them, and
        # nothing releases it with time.
        already_billed = patient_responsibility_billed(charges, stored)
        difference = posting.patient_responsibility_cents - already_billed
        if disagreement is not None:
            logger.info(
                "remittance_ledger_withheld claim_id=%s reason=%s amount_cents=%d",
                stored.id,
                disagreement.reason,
                difference,
            )
        elif difference:
            charges.add_ledger_row(
                patient_id=stored.patient_id,
                kind=PATIENT_RESPONSIBILITY_KIND,
                amount_cents=difference,
                currency=DEFAULT_CHARGE_CURRENCY,
                user_id=pipeline.principal_user_id,
                claim_id=stored.id,
                note=f"payer remittance {posting.source_id}",
            )

    logger.info(
        "remittance_posted claim_id=%s event=%s paid_cents=%d patient_resp_cents=%d",
        stored.id,
        posting.event,
        posting.paid_cents,
        posting.patient_responsibility_cents,
    )
    return stored, True


def post_remittances(
    pipeline: ClaimPipeline,
    timelines: ClaimTimelineSource,
    claims: Iterable[Claim],
    *,
    charges: PatientPaymentRepository | None = None,
    details: RemittanceDetailSource | None = None,
) -> int:
    """Read each claim's timeline and post whatever the payer decided.

    Returns how many claims moved.

    One claim's clearinghouse failing is not the pass failing: the others are
    still worth reading, and a claim that could not be read this time is read
    again on the next pass. A claim the clearinghouse has no id for was never
    filed through it and has no timeline to ask about.

    ``details`` is the service-line half, read from the 835 itself. It is
    optional and failing to read it never blocks the posting: knowing a claim
    was paid is worth recording even when the breakdown could not be
    fetched, and the alternative — refusing to post the money because the
    detail was unavailable — would leave a paid claim looking unpaid.
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
        detail = None
        if details is not None:
            try:
                detail = details.detail_for(claim.control_number)
            except ClearinghouseError:
                logger.warning("remittance_detail_read_failed claim_id=%s", claim.id)
        _, did_move = apply_posting(pipeline, claim, posting, charges=charges, detail=detail)
        moved += int(did_move)
    return moved
