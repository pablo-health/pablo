# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a payer's remittance does to a claim.

Every case here is one a practice would notice if it were wrong: a claim
filed away as denied when the client actually owes the money, a claim shown
as paid on the strength of an estimate, a reversal that left the total
overstated.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.claims.remittance import posting_for
from app.models.claims_timeline import ClaimTimeline, TimelinePayment

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
