# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a payer's remittance does to a claim.

Every case here is one a practice would notice if it were wrong: a claim
filed away as denied when the client actually owes the money, a claim shown
as paid on the strength of an estimate, a reversal that left the total
overstated.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.claims.remittance import apply_posting, posting_for
from app.models.claims_timeline import ClaimTimeline, TimelinePayment

from .claims_pipeline_fakes import make_harness, restore_listeners

_EARLIER = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
_LATER = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)

CHARGED = 15000


def _payment(
    *,
    disposition: str,
    paid: int,
    responsibility: int | None = None,
    trace: str | None = None,
    at: datetime = _LATER,
    payment_id: str = "clp_1",
) -> TimelinePayment:
    return TimelinePayment(
        id=payment_id,
        disposition=disposition,
        charged_cents=CHARGED,
        paid_cents=paid,
        patient_responsibility_cents=responsibility,
        trace_number=trace,
        processed_at=at,
    )


def _timeline(*payments: TimelinePayment) -> ClaimTimeline:
    return ClaimTimeline(payments=list(payments))


class TestNothingToPost:
    def test_a_claim_with_no_remittance_yet_is_left_alone(self) -> None:
        assert posting_for(_timeline(), charged_cents=CHARGED) is None

    def test_a_predetermination_alone_does_not_adjudicate_the_claim(self) -> None:
        """It is a price, not a decision. The claim keeps waiting."""
        timeline = _timeline(_payment(disposition="estimate", paid=8000, responsibility=2000))

        assert posting_for(timeline, charged_cents=CHARGED) is None

    def test_a_claim_forwarded_to_another_payer_is_not_adjudicated(self) -> None:
        timeline = _timeline(_payment(disposition="forwarded", paid=0))

        assert posting_for(timeline, charged_cents=CHARGED) is None


class TestPaid:
    def test_the_full_charge_pays_the_claim(self) -> None:
        timeline = _timeline(_payment(disposition="paid", paid=CHARGED, trace="EFT1"))

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.event == "pay"
        assert posting.paid_cents == CHARGED
        assert posting.trace_number == "EFT1"
        assert posting.adjudicated_at == _LATER

    def test_more_than_the_charge_still_pays(self) -> None:
        """Payers do overpay; it is not a reason to leave the claim open."""
        timeline = _timeline(_payment(disposition="paid", paid=CHARGED + 100))

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.event == "pay"

    def test_less_than_the_charge_is_partial(self) -> None:
        timeline = _timeline(_payment(disposition="paid", paid=8000, responsibility=2000))

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.event == "pay_partial"
        assert posting.paid_cents == 8000
        assert posting.patient_responsibility_cents == 2000


class TestZeroPaidIsNotADenial:
    def test_the_whole_charge_going_to_deductible_is_partial(self) -> None:
        """The payer processed it and paid nothing; the client owes the money.

        Calling this denied would file the claim away as a loss to appeal
        while the balance never reaches the client.
        """
        timeline = _timeline(
            _payment(disposition="paid", paid=0, responsibility=CHARGED, trace="EFT9")
        )

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.event == "pay_partial"
        assert posting.paid_cents == 0
        assert posting.patient_responsibility_cents == CHARGED


class TestDenied:
    def test_a_denial_denies(self) -> None:
        timeline = _timeline(_payment(disposition="denied", paid=0))

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.event == "deny"
        assert posting.paid_cents == 0
        assert posting.trace_number is None

    def test_a_denial_after_a_payment_does_not_undo_the_payment(self) -> None:
        """Only a reversal takes money back; a second-line denial does not."""
        timeline = _timeline(
            _payment(disposition="paid", paid=8000, at=_EARLIER, trace="EFT1"),
            _payment(disposition="denied", paid=0, at=_LATER, payment_id="clp_2"),
        )

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.event == "pay_partial"
        assert posting.paid_cents == 8000


class TestReversal:
    def test_a_reversal_is_subtracted_from_the_total(self) -> None:
        timeline = _timeline(
            _payment(disposition="paid", paid=8000, at=_EARLIER, trace="EFT1"),
            _payment(
                disposition="reversed", paid=-8000, at=_LATER, trace="EFT2", payment_id="clp_2"
            ),
        )

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.paid_cents == 0
        assert posting.event == "pay_partial"

    def test_the_trace_number_is_the_most_recent_one_that_moved_money(self) -> None:
        timeline = _timeline(
            _payment(disposition="paid", paid=5000, at=_EARLIER, trace="EFT1"),
            _payment(disposition="paid", paid=3000, at=_LATER, trace="EFT2", payment_id="clp_2"),
        )

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.trace_number == "EFT2"
        assert posting.paid_cents == 8000


@pytest.fixture
def harness():
    made = make_harness()
    yield made
    restore_listeners()


def _paid_posting(**overrides):
    timeline = _timeline(_payment(disposition="paid", paid=8000, trace="EFT1", **overrides))
    posting = posting_for(timeline, charged_cents=CHARGED)
    assert posting is not None
    return posting


class TestApplyingAPosting:
    def test_the_claim_moves_and_records_what_the_payer_did(self, harness) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)

        stored, moved = apply_posting(harness.pipeline, claim, _paid_posting())

        assert moved is True
        assert stored.state == "partial"
        assert stored.total_paid_cents == 8000
        receipt = harness.receipts.list_for_claim(claim.id)[-1]
        assert receipt.kind == "adjudicated"
        assert receipt.detail["paid_cents"] == 8000
        assert receipt.detail["trace_number"] == "EFT1"

    def test_reading_the_same_remittance_twice_posts_once(self, harness) -> None:
        """Reading is cheap and will run on a schedule; paying twice is not."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        posting = _paid_posting()

        first, moved_first = apply_posting(harness.pipeline, claim, posting)
        second, moved_second = apply_posting(harness.pipeline, first, posting)

        assert (moved_first, moved_second) == (True, False)
        assert second.total_paid_cents == 8000
        kinds = [r.kind for r in harness.receipts.list_for_claim(claim.id)]
        assert kinds.count("adjudicated") == 1

    def test_a_claim_already_closed_is_left_alone(self, harness) -> None:
        """A second remittance on a settled claim is news, not a reason to fail."""
        claim = harness.add(state="paid", total_charge_cents=CHARGED)

        stored, moved = apply_posting(harness.pipeline, claim, _paid_posting())

        assert moved is False
        assert stored.state == "paid"

    def test_a_full_payment_pays_the_claim(self, harness) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        timeline = _timeline(_payment(disposition="paid", paid=CHARGED, trace="EFT1"))
        posting = posting_for(timeline, charged_cents=CHARGED)
        assert posting is not None

        stored, moved = apply_posting(harness.pipeline, claim, posting)

        assert moved is True
        assert stored.state == "paid"
        assert stored.total_paid_cents == CHARGED

    def test_a_denial_denies_the_claim(self, harness) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        timeline = _timeline(_payment(disposition="denied", paid=0))
        posting = posting_for(timeline, charged_cents=CHARGED)
        assert posting is not None

        stored, moved = apply_posting(harness.pipeline, claim, posting)

        assert moved is True
        assert stored.state == "denied"
        assert stored.total_paid_cents == 0

    def test_a_stalled_claim_can_still_be_paid(self, harness) -> None:
        """A stalled claim is one nobody has heard from, not one that is over."""
        claim = harness.add(state="stalled", total_charge_cents=CHARGED)

        stored, moved = apply_posting(harness.pipeline, claim, _paid_posting())

        assert moved is True
        assert stored.state == "partial"
