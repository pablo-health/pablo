# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a payer's remittance does to a claim.

Every case here is one a practice would notice if it were wrong: a claim
filed away as denied when the client actually owes the money, a claim shown
as paid on the strength of an estimate, a reversal that left the total
overstated.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.claims.clearinghouse import ClearinghouseUnavailableError
from app.claims.remittance import apply_posting, post_remittances, posting_for
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


class TestWhatTheClientOwesAcrossPayers:
    """Each payer states the balance after it finished, so the last one wins.

    A secondary never restates the primary's assignment — it reports only
    what it assigned itself. Adding them together bills one session twice.
    """

    def test_the_latest_adjudication_states_the_balance(self) -> None:
        timeline = _timeline(
            _payment(disposition="paid", paid=12000, responsibility=3000, at=_EARLIER),
            _payment(
                disposition="paid",
                paid=2000,
                responsibility=1000,
                at=_LATER,
                payment_id="clp_2",
            ),
        )

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.patient_responsibility_cents == 1000, (
            "the secondary assigned $10; summing both payers would say $40"
        )
        assert posting.paid_cents == 14000, "money paid does still add up"

    def test_a_secondary_that_covers_the_coinsurance_leaves_nothing_owing(self) -> None:
        timeline = _timeline(
            _payment(disposition="paid", paid=12000, responsibility=3000, at=_EARLIER),
            _payment(
                disposition="paid",
                paid=3000,
                responsibility=0,
                at=_LATER,
                payment_id="clp_2",
            ),
        )

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.patient_responsibility_cents == 0

    def test_a_denial_can_still_leave_the_client_owing_everything(self) -> None:
        """A service the plan does not cover is denied, and the client owes
        the charge. Counting only entries that moved money reported zero."""
        timeline = _timeline(_payment(disposition="denied", paid=0, responsibility=CHARGED))

        posting = posting_for(timeline, charged_cents=CHARGED)

        assert posting is not None
        assert posting.event == "deny"
        assert posting.patient_responsibility_cents == CHARGED


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

    def test_a_settled_claim_can_be_adjudicated_again(self, harness) -> None:
        """Because a second payer, or a takeback, really does arrive later.

        A paid claim that a reversal reduces is partly paid, and the claim
        has to say so. Leaving it alone was how a client kept owing a
        balance their secondary had already covered.
        """
        claim = harness.add(state="paid", total_charge_cents=CHARGED)

        stored, moved = apply_posting(harness.pipeline, claim, _paid_posting())

        assert moved is True
        assert stored.state == "partial"

    def test_a_rejected_claim_is_left_alone(self, harness) -> None:
        """A remittance for a claim that never reached a payer is news, not a
        reason to fail the pass. The answer to a rejection is a corrected
        claim of its own, not another event on this one."""
        claim = harness.add(state="rejected", total_charge_cents=CHARGED)

        stored, moved = apply_posting(harness.pipeline, claim, _paid_posting())

        assert moved is False
        assert stored.state == "rejected"

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


class _Ledger:
    """Just enough of the charge repository to see what was written.

    Reads back what it wrote, because the posting now asks: what it puts on a
    client's ledger is the difference from what that claim already billed.
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add_ledger_row(self, **row):
        self.rows.append(row)
        return row

    def list_charges(self, patient_id: str):
        return [
            SimpleNamespace(
                amount_cents=row["amount_cents"],
                claim_id=row.get("claim_id"),
                kind=row["kind"],
            )
            for row in self.rows
            if row["patient_id"] == patient_id
        ]

    @property
    def total_billed(self) -> int:
        """What the client is left owing across every row written."""
        return sum(row["amount_cents"] for row in self.rows)


class TestTheClientsShareReachesTheirLedger:
    def test_what_the_payer_says_the_client_owes_becomes_a_ledger_row(self, harness) -> None:
        """Otherwise the money stops at the claim and nobody bills the client."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        timeline = _timeline(_payment(disposition="paid", paid=8000, responsibility=2000))
        posting = posting_for(timeline, charged_cents=CHARGED)
        assert posting is not None

        apply_posting(harness.pipeline, claim, posting, charges=ledger)

        assert len(ledger.rows) == 1
        row = ledger.rows[0]
        assert row["kind"] == "patient_resp"
        assert row["amount_cents"] == 2000
        assert row["claim_id"] == claim.id
        assert row["patient_id"] == claim.patient_id

    def test_a_client_who_owes_nothing_gets_no_row(self, harness) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        timeline = _timeline(_payment(disposition="paid", paid=CHARGED, responsibility=None))
        posting = posting_for(timeline, charged_cents=CHARGED)
        assert posting is not None

        apply_posting(harness.pipeline, claim, posting, charges=ledger)

        assert ledger.rows == []

    def test_reading_the_same_remittance_twice_bills_the_client_once(self, harness) -> None:
        """The receipt's idempotency has to cover the ledger write too."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        timeline = _timeline(_payment(disposition="paid", paid=8000, responsibility=2000))
        posting = posting_for(timeline, charged_cents=CHARGED)
        assert posting is not None

        first, _ = apply_posting(harness.pipeline, claim, posting, charges=ledger)
        apply_posting(harness.pipeline, first, posting, charges=ledger)

        assert len(ledger.rows) == 1

    def test_the_whole_charge_going_to_deductible_bills_the_whole_charge(self, harness) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        timeline = _timeline(_payment(disposition="paid", paid=0, responsibility=CHARGED))
        posting = posting_for(timeline, charged_cents=CHARGED)
        assert posting is not None

        apply_posting(harness.pipeline, claim, posting, charges=ledger)

        assert ledger.rows[0]["amount_cents"] == CHARGED


class TestASecondPayerDoesNotBillTheClientTwice:
    """A claim with secondary coverage gets a remittance from each payer.

    Each one states what the client owes *after that payer adjudicated* — the
    standard has a secondary report only the responsibility it assigned
    itself, never the primary's. So a remittance restates the balance, and
    adding them together bills one session twice.
    """

    def _post(self, harness, claim, ledger, *, responsibility: int, entry: str):
        timeline = _timeline(
            _payment(
                disposition="paid",
                paid=CHARGED - responsibility,
                responsibility=responsibility,
                payment_id=entry,
            )
        )
        posting = posting_for(timeline, charged_cents=CHARGED)
        assert posting is not None
        stored, _ = apply_posting(harness.pipeline, claim, posting, charges=ledger)
        return stored

    def test_a_secondary_that_pays_the_coinsurance_leaves_the_client_owing_nothing(
        self, harness
    ) -> None:
        """The case that was silently wrong.

        Primary assigns $30, secondary pays it and assigns nothing. The client
        owes nothing — which means writing a credit, not leaving $30 standing.
        """
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()

        after_primary = self._post(harness, claim, ledger, responsibility=3000, entry="clp_1")
        self._post(harness, after_primary, ledger, responsibility=0, entry="clp_2")

        assert ledger.total_billed == 0
        assert [row["amount_cents"] for row in ledger.rows] == [3000, -3000]

    def test_a_secondary_that_assigns_less_leaves_only_its_own_share(self, harness) -> None:
        """Primary says $30, secondary says $10. The client owes $10, not $40."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()

        after_primary = self._post(harness, claim, ledger, responsibility=3000, entry="clp_1")
        self._post(harness, after_primary, ledger, responsibility=1000, entry="clp_2")

        assert ledger.total_billed == 1000

    def test_a_secondary_that_assigns_more_bills_only_the_difference(self, harness) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()

        after_primary = self._post(harness, claim, ledger, responsibility=1000, entry="clp_1")
        self._post(harness, after_primary, ledger, responsibility=3000, entry="clp_2")

        assert ledger.total_billed == 3000
        assert [row["amount_cents"] for row in ledger.rows] == [1000, 2000]

    def test_a_remittance_that_changes_nothing_writes_nothing(self, harness) -> None:
        """A second payer agreeing with the first is not a second bill."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()

        after_primary = self._post(harness, claim, ledger, responsibility=3000, entry="clp_1")
        self._post(harness, after_primary, ledger, responsibility=3000, entry="clp_2")

        assert len(ledger.rows) == 1
        assert ledger.total_billed == 3000

    def test_another_claims_rows_are_not_counted_against_this_one(self, harness) -> None:
        """The same client can have two claims open; each carries its own
        balance and one must not offset the other."""
        ledger = _Ledger()
        first = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        second = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        assert first.patient_id == second.patient_id

        self._post(harness, first, ledger, responsibility=3000, entry="clp_1")
        self._post(harness, second, ledger, responsibility=2000, entry="clp_2")

        assert ledger.total_billed == 5000


class _Timelines:
    """A stand-in clearinghouse: what it knows, keyed by vendor claim id."""

    def __init__(self, **by_id: ClaimTimeline) -> None:
        self._by_id = by_id
        self.asked: list[str] = []

    def timeline_for(self, vendor_claim_id: str) -> ClaimTimeline:
        self.asked.append(vendor_claim_id)
        found = self._by_id.get(vendor_claim_id)
        if found is None:
            raise ClearinghouseUnavailableError(vendor_claim_id)
        return found


class TestThePass:
    def test_each_claim_is_read_and_posted(self, harness) -> None:
        first = harness.add(
            state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="v1"
        )
        second = harness.add(
            state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="v2"
        )
        timelines = _Timelines(
            v1=_timeline(_payment(disposition="paid", paid=CHARGED)),
            # Deliberately the same entry id as v1's: the receipt key is
            # scoped to the claim, so one claim's payment must not swallow
            # another's.
            v2=_timeline(_payment(disposition="denied", paid=0)),
        )

        moved = post_remittances(harness.pipeline, timelines, [first, second])

        assert moved == 2
        assert harness.get(first.id).state == "paid"
        assert harness.get(second.id).state == "denied"

    def test_a_claim_the_clearinghouse_never_filed_is_not_asked_about(self, harness) -> None:
        claim = harness.add(state="validated", total_charge_cents=CHARGED, vendor_claim_id=None)
        timelines = _Timelines()

        assert post_remittances(harness.pipeline, timelines, [claim]) == 0
        assert timelines.asked == []

    def test_one_claim_failing_does_not_stop_the_others(self, harness) -> None:
        """The next pass reads it again; the rest are still worth posting."""
        broken = harness.add(
            state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="missing"
        )
        fine = harness.add(state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="v2")
        timelines = _Timelines(v2=_timeline(_payment(disposition="paid", paid=CHARGED)))

        moved = post_remittances(harness.pipeline, timelines, [broken, fine])

        assert moved == 1
        assert harness.get(broken.id).state == "payer_accepted"
        assert harness.get(fine.id).state == "paid"

    def test_a_claim_the_payer_has_not_decided_on_is_left_alone(self, harness) -> None:
        claim = harness.add(
            state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="v1"
        )
        timelines = _Timelines(v1=ClaimTimeline())

        assert post_remittances(harness.pipeline, timelines, [claim]) == 0
        assert harness.get(claim.id).state == "payer_accepted"

    def test_running_the_pass_twice_posts_once(self, harness) -> None:
        """It runs on a schedule; the second read must be a no-op."""
        claim = harness.add(
            state="payer_accepted", total_charge_cents=CHARGED, vendor_claim_id="v1"
        )
        timelines = _Timelines(v1=_timeline(_payment(disposition="paid", paid=CHARGED)))

        first = post_remittances(harness.pipeline, timelines, [claim])
        second = post_remittances(harness.pipeline, timelines, [harness.get(claim.id)])

        assert (first, second) == (1, 0)
        assert harness.get(claim.id).total_paid_cents == CHARGED
