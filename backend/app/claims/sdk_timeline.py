# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a claim's timeline from the vendor's claim-lifecycle API.

Knowing whether a claim was paid used to mean polling a shared feed of every
transaction the account has seen, fetching each document by id, and matching
it back to a claim by control number. The vendor now keeps that history per
claim, so this asks the claim directly and translates the answer.

What arrives is a tagged union: each entry is a submission, an
acknowledgement, or what a payer reported paying. Entry kinds this practice
does not file — dental and institutional claims — are read as submissions
anyway rather than dropped, because a timeline that silently omitted an entry
would misreport the claim's history; and an entry kind added after this was
written falls through to nothing rather than raising.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models.claims_timeline import (
    AcknowledgmentOutcome,
    ClaimTimeline,
    PaymentDisposition,
    TimelineAcknowledgment,
    TimelinePayment,
    TimelineSubmission,
)
from ..money import dollars_to_cents

if TYPE_CHECKING:
    from stedi.models import GetClaimTimelineOutput

#: How the vendor's payment status codes read as money.
#:
#: ``PREDETERMINATION_PRICING_ONLY`` is the dangerous one: the payer is
#: pricing a claim it has not adjudicated, and the entry carries amounts that
#: look exactly like a payment. Posting it would credit a client for money
#: nobody sent.
_DISPOSITIONS: dict[str, PaymentDisposition] = {
    "PROCESSED_AS_PRIMARY": "paid",
    "PROCESSED_AS_PRIMARY_FORWARDED_TO_ADDITIONAL_PAYERS": "paid",
    "PROCESSED_AS_SECONDARY": "paid",
    "PROCESSED_AS_SECONDARY_FORWARDED_TO_ADDITIONAL_PAYERS": "paid",
    "PROCESSED_AS_TERTIARY": "paid",
    "PROCESSED_AS_TERTIARY_FORWARDED_TO_ADDITIONAL_PAYERS": "paid",
    "DENIED": "denied",
    "REVERSAL_OF_PREVIOUS_PAYMENT": "reversed",
    "NOT_OUR_CLAIM_FORWARDED_TO_ADDITIONAL_PAYERS": "forwarded",
    "PREDETERMINATION_PRICING_ONLY": "estimate",
}

#: A 277CA's acknowledgement status, as the tracker's four words. A status
#: this does not recognise reads as ``unknown``, which shows the claim as
#: still in flight rather than inventing an outcome for it.
_OUTCOMES: dict[str, AcknowledgmentOutcome] = {
    "ACCEPTED": "accepted",
    "REJECTED": "rejected",
    "PENDING": "pending",
}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _cents(amount: str | None) -> int | None:
    """A vendor decimal string as cents, negatives included.

    A reversal is reported as a negative amount, so this must not clamp.
    """
    if amount is None:
        return None
    try:
        return dollars_to_cents(amount)
    except ValueError:
        return None


def _submission(summary: Any) -> TimelineSubmission:
    return TimelineSubmission(
        id=summary.id,
        patient_control_number=summary.patient_control_number,
        charged_cents=_cents(summary.total_claim_charge_amount) or 0,
        processed_at=summary.processed_at,
    )


def _acknowledgment(summary: Any) -> TimelineAcknowledgment:
    return TimelineAcknowledgment(
        id=summary.id,
        outcome=_OUTCOMES.get(_enum_value(summary.status), "unknown"),
        reported_by=_enum_value(summary.reported_by),
        source_name=summary.source_name,
        processed_at=summary.processed_at,
    )


def _payment(summary: Any) -> TimelinePayment:
    """One ``CLP`` loop from a remittance.

    A status code this mapping has never seen reads as ``estimate`` rather
    than ``paid``: the conservative reading is that no money moved, because
    the cost of inventing a payment is a client credited for funds nobody
    sent, and the cost of missing one is a claim that stays open.
    """
    return TimelinePayment(
        id=summary.id,
        disposition=_DISPOSITIONS.get(_enum_value(summary.status_code), "estimate"),
        charged_cents=_cents(summary.total_claim_charge_amount) or 0,
        paid_cents=_cents(summary.claim_payment_amount) or 0,
        patient_responsibility_cents=_cents(summary.patient_responsibility_amount),
        trace_number=summary.check_or_eft_trace_number,
        processed_at=summary.processed_at,
    )


def timeline_from_sdk(output: GetClaimTimelineOutput) -> ClaimTimeline:
    """The vendor's timeline entries, sorted into what each one is."""
    timeline = ClaimTimeline(next_page_token=output.next_page_token)
    for item in output.items:
        value = getattr(item, "value", None)
        if value is None:
            continue
        match type(item).__name__:
            case (
                "ClaimTimelineEventProfessionalClaimSubmission"
                | "ClaimTimelineEventDentalClaimSubmission"
                | "ClaimTimelineEventInstitutionalClaimSubmission"
            ):
                timeline.submissions.append(_submission(value))
            case "ClaimTimelineEventClaimAcknowledgment":
                timeline.acknowledgments.append(_acknowledgment(value))
            case "ClaimTimelineEventClaimPaymentInformation":
                timeline.payments.append(_payment(value))
            case _:
                # An entry kind added after this was written. Ignored rather
                # than raised on, so a new one cannot take the tracker down.
                continue
    return timeline
