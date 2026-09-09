# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a claim's timeline, and deciding what counts as money.

The arithmetic here decides whether a practice believes it has been paid, so
the cases that matter are the ones where an entry looks like a payment and
is not: a predetermination the payer priced but never adjudicated, a denial
carrying a zero, a reversal that has to subtract.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from app.claims.sdk_timeline import fetch_timeline, timeline_from_sdk
from stedi.models import (
    ClaimAcknowledgmentSummary,
    ClaimPaymentInformationSummary,
    ClaimSubmissionSummary,
    ClaimTimelineEventClaimAcknowledgment,
    ClaimTimelineEventClaimPaymentInformation,
    ClaimTimelineEventProfessionalClaimSubmission,
    GetClaimTimelineOutput,
)

_AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _payment_event(
    *,
    status: str,
    paid: str = "80.00",
    charged: str = "150.00",
    responsibility: str | None = "20.00",
    trace: str | None = "EFT12345",
):
    return ClaimTimelineEventClaimPaymentInformation(
        ClaimPaymentInformationSummary(
            id="clp_1",
            status_code=status,
            total_claim_charge_amount=charged,
            claim_payment_amount=paid,
            patient_responsibility_amount=responsibility,
            check_or_eft_trace_number=trace,
            processed_at=_AT,
        )
    )


def _timeline(*items) -> GetClaimTimelineOutput:
    return GetClaimTimelineOutput(items=list(items))


class TestWhatCountsAsMoney:
    @pytest.mark.parametrize(
        "status",
        [
            "PROCESSED_AS_PRIMARY",
            "PROCESSED_AS_SECONDARY",
            "PROCESSED_AS_TERTIARY",
            "PROCESSED_AS_PRIMARY_FORWARDED_TO_ADDITIONAL_PAYERS",
        ],
    )
    def test_an_adjudicated_claim_is_a_payment(self, status: str) -> None:
        timeline = timeline_from_sdk(_timeline(_payment_event(status=status)))

        assert timeline.payments[0].disposition == "paid"
        assert timeline.paid_cents == 8000

    def test_a_predetermination_is_not_money(self) -> None:
        """The trap: it is priced like a payment but nobody sent anything."""
        timeline = timeline_from_sdk(
            _timeline(_payment_event(status="PREDETERMINATION_PRICING_ONLY"))
        )

        assert timeline.payments[0].disposition == "estimate"
        assert timeline.paid_cents == 0
        assert timeline.patient_responsibility_cents == 0

    def test_a_denial_pays_nothing(self) -> None:
        timeline = timeline_from_sdk(_timeline(_payment_event(status="DENIED", paid="0.00")))

        assert timeline.payments[0].disposition == "denied"
        assert timeline.paid_cents == 0

    def test_a_claim_forwarded_elsewhere_pays_nothing(self) -> None:
        timeline = timeline_from_sdk(
            _timeline(
                _payment_event(status="NOT_OUR_CLAIM_FORWARDED_TO_ADDITIONAL_PAYERS", paid="0.00")
            )
        )

        assert timeline.payments[0].disposition == "forwarded"
        assert timeline.paid_cents == 0

    def test_a_reversal_subtracts(self) -> None:
        """A payer taking a payment back reports it as a negative amount."""
        timeline = timeline_from_sdk(
            _timeline(
                _payment_event(status="PROCESSED_AS_PRIMARY", paid="80.00"),
                _payment_event(status="REVERSAL_OF_PREVIOUS_PAYMENT", paid="-80.00"),
            )
        )

        assert [p.disposition for p in timeline.payments] == ["paid", "reversed"]
        assert timeline.paid_cents == 0

    def test_an_unknown_status_is_not_treated_as_paid(self) -> None:
        """A code added later must not silently credit a client."""
        timeline = timeline_from_sdk(_timeline(_payment_event(status="SOMETHING_NEW")))

        assert timeline.payments[0].disposition == "estimate"
        assert timeline.paid_cents == 0

    def test_patient_responsibility_only_counts_where_money_moved(self) -> None:
        timeline = timeline_from_sdk(
            _timeline(
                _payment_event(status="PROCESSED_AS_PRIMARY", responsibility="20.00"),
                _payment_event(status="PREDETERMINATION_PRICING_ONLY", responsibility="99.00"),
            )
        )

        assert timeline.patient_responsibility_cents == 2000


class TestTheOtherEntries:
    def test_a_submission_is_read(self) -> None:
        event = ClaimTimelineEventProfessionalClaimSubmission(
            ClaimSubmissionSummary(
                id="sub_1",
                patient_control_number="PCN-1",
                total_claim_charge_amount="150.00",
                processed_at=_AT,
            )
        )

        timeline = timeline_from_sdk(_timeline(event))

        assert timeline.submissions[0].patient_control_number == "PCN-1"
        assert timeline.submissions[0].charged_cents == 15000

    @pytest.mark.parametrize(
        ("status", "outcome"),
        [("ACCEPTED", "accepted"), ("REJECTED", "rejected"), ("PENDING", "pending")],
    )
    def test_an_acknowledgment_is_read(self, status: str, outcome: str) -> None:
        event = ClaimTimelineEventClaimAcknowledgment(
            ClaimAcknowledgmentSummary(
                id="ack_1",
                status=status,
                reported_by="PAYER",
                source_name="Aetna",
                processed_at=_AT,
            )
        )

        timeline = timeline_from_sdk(_timeline(event))

        assert timeline.acknowledgments[0].outcome == outcome
        assert timeline.acknowledgments[0].reported_by == "PAYER"

    def test_an_unrecognised_acknowledgment_status_leaves_the_claim_in_flight(self) -> None:
        event = ClaimTimelineEventClaimAcknowledgment(
            ClaimAcknowledgmentSummary(
                id="ack_1",
                status="SOMETHING_NEW",
                reported_by="CLEARINGHOUSE",
                source_name="Stedi",
                processed_at=_AT,
            )
        )

        assert timeline_from_sdk(_timeline(event)).acknowledgments[0].outcome == "unknown"


class TestFetchingEveryPage:
    """A one-page read would be wrong, not merely incomplete."""

    class _PagingClient:
        def __init__(self, pages: list[GetClaimTimelineOutput]) -> None:
            self._pages = pages
            self.seen_tokens: list[str | None] = []

        async def get_claim_timeline(self, inp) -> GetClaimTimelineOutput:
            self.seen_tokens.append(inp.page_token)
            return self._pages[len(self.seen_tokens) - 1]

    def test_a_reversal_on_a_later_page_still_subtracts(self) -> None:
        client = self._PagingClient(
            [
                GetClaimTimelineOutput(
                    items=[_payment_event(status="PROCESSED_AS_PRIMARY", paid="80.00")],
                    next_page_token="page-2",  # noqa: S106 - a cursor, not a credential
                ),
                GetClaimTimelineOutput(
                    items=[_payment_event(status="REVERSAL_OF_PREVIOUS_PAYMENT", paid="-80.00")]
                ),
            ]
        )

        timeline = asyncio.run(fetch_timeline(client, "clm_1"))

        assert client.seen_tokens == [None, "page-2"]
        assert len(timeline.payments) == 2
        assert timeline.paid_cents == 0

    def test_a_vendor_that_never_stops_paging_is_cut_off(self) -> None:
        """Reporting what we have beats looping forever."""
        endless = GetClaimTimelineOutput(
            items=[],
            next_page_token="always",  # noqa: S106 - a cursor, not a credential
        )
        client = self._PagingClient([endless] * 10)

        timeline = asyncio.run(fetch_timeline(client, "clm_1", max_pages=3))

        assert len(client.seen_tokens) == 3
        assert timeline.next_page_token == "always"


class TestPaging:
    def test_the_page_token_comes_through(self) -> None:
        # S106 matches on the argument name; a paging cursor is not a credential.
        output = GetClaimTimelineOutput(items=[], next_page_token="page-2")  # noqa: S106

        assert timeline_from_sdk(output).next_page_token == "page-2"

    def test_an_empty_timeline_is_not_an_error(self) -> None:
        """A claim filed a minute ago has no entries yet."""
        timeline = timeline_from_sdk(GetClaimTimelineOutput(items=[]))

        assert timeline.paid_cents == 0
        assert not timeline.submissions
