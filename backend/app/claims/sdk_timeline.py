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

import logging
from typing import TYPE_CHECKING, Any, Protocol

from ..models.claims_timeline import (
    AcknowledgmentOutcome,
    ClaimTimeline,
    PaymentDisposition,
    TimelineAcknowledgment,
    TimelinePayment,
    TimelineSubmission,
)
from ..money import dollars_to_cents
from .clearinghouse import ClearinghouseError
from .sdk_runtime import run_on_sdk_loop, translate_sdk_error
from .stedi_sdk import client_for

if TYPE_CHECKING:
    from stedi.models import GetClaimTimelineOutput

    from .credentials import ClearinghouseCredentials

logger = logging.getLogger(__name__)

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


class ClaimTimelineSource(Protocol):
    """Where a claim's history comes from.

    A narrow seam of its own rather than another method on
    ``ClearinghouseClient``: only the vendor's newer claim-lifecycle API can
    answer this, and giving the older adapter a method it would have to
    refuse is worse than letting callers ask for the capability they need.
    """

    def timeline_for(self, vendor_claim_id: str) -> ClaimTimeline:
        """Everything the clearinghouse knows about this claim."""
        ...


class SdkClaimTimelines:
    """Claim timelines, read through the vendor SDK from synchronous callers."""

    def __init__(self, credentials: ClearinghouseCredentials) -> None:
        self._credentials = credentials

    def timeline_for(self, vendor_claim_id: str) -> ClaimTimeline:
        async def read() -> ClaimTimeline:
            client = await client_for(self._credentials)
            return await fetch_timeline(client, vendor_claim_id)

        try:
            return run_on_sdk_loop(read())
        except ClearinghouseError:
            raise
        except Exception as exc:
            raise translate_sdk_error(exc) from exc


async def fetch_timeline(client: Any, claim_id: str, *, max_pages: int = 20) -> ClaimTimeline:
    """Every page of one claim's timeline, read through the vendor's SDK.

    Paged eagerly rather than lazily: the caller is deciding what a payer did
    to a claim, and an answer built from the first page only would be wrong
    rather than incomplete — a reversal on page two subtracts from a payment
    on page one.

    ``max_pages`` is a stop, not a budget. A claim with more entries than
    this has something wrong with it, and looping forever on a vendor that
    keeps handing back a cursor is worse than reporting what we have.
    """
    from stedi.models import GetClaimTimelineInput  # noqa: PLC0415 — vendor import at call time

    combined = ClaimTimeline()
    page_token: str | None = None
    for _ in range(max_pages):
        output = await client.get_claim_timeline(
            GetClaimTimelineInput(id=claim_id, page_token=page_token)
        )
        page = timeline_from_sdk(output)
        combined.submissions.extend(page.submissions)
        combined.acknowledgments.extend(page.acknowledgments)
        combined.payments.extend(page.payments)
        page_token = page.next_page_token
        if not page_token:
            return combined
    logger.warning("claim_timeline_pages_exhausted claim_id=%s pages=%d", claim_id, max_pages)
    combined.next_page_token = page_token
    return combined


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
