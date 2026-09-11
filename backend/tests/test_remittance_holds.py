# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refusing to bill a client from a remittance that contradicts itself.

The bug these cover is not a crash. It is a real person receiving a bill for
an amount the engine's own arithmetic says is wrong, and nobody finding out
— which is why almost every assertion below is about something that did
*not* happen.

The shape to hold on to: a hold changes exactly one thing. The payer's
payment still posts, the claim still moves, the receipt still says
adjudicated. Only the row on the client's ledger is withheld. Tests that
assert the withholding without also asserting the posting would pass on an
implementation that simply dropped the remittance, which would be a worse
bug than the one being fixed.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.claims.holds import codes_of, record, withheld_cents
from app.claims.remittance import apply_posting, posting_for
from app.models.claims_holds import RemittanceHold
from app.models.claims_responses import Adjustment, RemittanceClaim, RemittanceLine
from app.models.claims_timeline import ClaimTimeline, TimelinePayment
from app.repositories.remittance_hold import InMemoryRemittanceHoldRepository

from .claims_pipeline_fakes import make_harness, restore_listeners

CHARGED = 15_000
_AT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


@pytest.fixture
def harness():
    made = make_harness()
    yield made
    restore_listeners()


class _Ledger:
    """Just enough of the charge repository to see what was written."""

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


def _adjustment(group: str, reason: str, cents: int) -> Adjustment:
    return Adjustment(group_code=group, reason_code=reason, amount_cents=cents)


def _detail(
    control_number: str,
    *,
    paid_cents: int = 12_000,
    stated_responsibility: int = 3_000,
    line_adjustments: list[Adjustment] | None = None,
    claim_status_code: str = "1",
    charge_cents: int = CHARGED,
) -> RemittanceClaim:
    """One claim's 835 detail, balanced unless a caller unbalances it."""
    adjustments = (
        line_adjustments
        if line_adjustments is not None
        else [_adjustment("PR", "2", charge_cents - paid_cents)]
    )
    return RemittanceClaim(
        patient_control_number=control_number,
        payer_claim_control_number="P1",
        claim_status_code=claim_status_code,
        total_charge_cents=charge_cents,
        paid_cents=paid_cents,
        patient_responsibility_cents=stated_responsibility,
        claim_frequency_code="1",
        adjustments=[],
        lines=[
            RemittanceLine(
                line_control_number=f"{control_number}L1",
                service_date="20260901",
                cpt="90837",
                charge_cents=charge_cents,
                paid_cents=paid_cents,
                adjustments=adjustments,
            )
        ],
    )


def _posting(paid: int = 12_000, responsibility: int = 3_000):
    timeline = ClaimTimeline(
        payments=[
            TimelinePayment(
                id="clp_1",
                disposition="paid",
                charged_cents=CHARGED,
                paid_cents=paid,
                patient_responsibility_cents=responsibility,
                trace_number="EFT1",
                processed_at=_AT,
            )
        ]
    )
    posting = posting_for(timeline, charged_cents=CHARGED)
    assert posting is not None
    return posting


class TestADisagreeingRemittanceDoesNotBillTheClient:
    def test_the_payment_posts_and_the_ledger_row_does_not(self, harness) -> None:
        """The whole point, in one test.

        The payer's $120 is a fact — it landed in the practice's account,
        and refusing to record it would only make a paid claim look unpaid.
        What is in doubt is the client's $30, because the payer stated it
        and then itemised something else.
        """
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        # Itemised as a contractual write-off; stated as the client's.
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )

        stored, moved = apply_posting(
            harness.pipeline, claim, _posting(), charges=ledger, detail=detail
        )

        assert moved is True
        assert stored.state == "partial"
        assert stored.total_paid_cents == 12_000
        assert harness.receipts.list_for_claim(claim.id)[-1].kind == "adjudicated"
        assert ledger.rows == []

    def test_exactly_one_hold_is_left_carrying_both_numbers(self, harness) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )

        apply_posting(harness.pipeline, claim, _posting(), charges=_Ledger(), detail=detail)

        [hold] = harness.holds.list_open()
        assert hold.reason == "patient_responsibility"
        assert hold.stated_cents == 3_000
        assert hold.computed_cents == 0
        assert hold.delta_cents == 3_000
        assert hold.claim_id == claim.id
        assert hold.patient_id == claim.patient_id
        assert hold.control_number == claim.control_number
        assert hold.state == "open"
        assert hold.finding is None

    def test_the_hold_carries_the_codes_the_payer_sent(self, harness) -> None:
        """The first thing anybody triaging the disagreement asks for."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 2_000), _adjustment("PR", "2", 1_000)],
            stated_responsibility=3_000,
        )

        apply_posting(harness.pipeline, claim, _posting(), charges=_Ledger(), detail=detail)

        [hold] = harness.holds.list_open()
        assert hold.codes == [
            {"group_code": "CO", "reason_code": "45"},
            {"group_code": "PR", "reason_code": "2"},
        ]
        assert hold.line_count == 1
        assert hold.payer_name == claim.subscriber_snapshot.payer_name

    def test_a_line_that_does_not_balance_also_holds_the_bill(self, harness) -> None:
        """Not only the patient-responsibility check gates the ledger.

        A line whose adjustments do not account for its own gap is a
        document we cannot read, and its ``PR`` figure is no more
        trustworthy for having been added up correctly.
        """
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("PR", "2", 1_000)],
            stated_responsibility=1_000,
            paid_cents=12_000,
        )

        apply_posting(harness.pipeline, claim, _posting(), charges=ledger, detail=detail)

        assert ledger.rows == []
        [hold] = harness.holds.list_open()
        assert hold.reason == "line_balance"
        assert hold.line_control_number == f"{claim.control_number}L1"

    def test_the_withholding_is_logged_with_what_was_not_billed(
        self, harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )

        with caplog.at_level("INFO"):
            apply_posting(harness.pipeline, claim, _posting(), charges=_Ledger(), detail=detail)

        assert "remittance_ledger_withheld" in caplog.text
        assert "amount_cents=3000" in caplog.text


class TestTheAgreeingPathIsUnchanged:
    def test_a_balanced_remittance_writes_the_ledger_row_and_no_hold(self, harness) -> None:
        """The regression guard. Most remittances are fine and must stay fine."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        detail = _detail(claim.control_number)

        apply_posting(harness.pipeline, claim, _posting(), charges=ledger, detail=detail)

        assert len(ledger.rows) == 1
        assert ledger.rows[0]["amount_cents"] == 3_000
        assert ledger.rows[0]["kind"] == "patient_resp"
        assert harness.holds.list_open() == []

    def test_a_posting_with_no_835_to_read_behaves_as_before(self, harness) -> None:
        """No itemisation means no cross-check, not a hold.

        The timeline path can post a claim the 835 was never fetched for.
        Holding every one of those would stop billing on a gap in our own
        plumbing, which is a different problem and not this one's to solve.
        """
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()

        apply_posting(harness.pipeline, claim, _posting(), charges=ledger, detail=None)

        assert len(ledger.rows) == 1
        assert harness.holds.list_open() == []

    def test_a_reversal_is_never_held(self, harness) -> None:
        """A takeback's stated total is exempt from the itemisation check."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            charge_cents=-CHARGED,
            paid_cents=-12_000,
            line_adjustments=[_adjustment("PR", "2", -3_000)],
            stated_responsibility=0,
            claim_status_code="22",
        )

        apply_posting(harness.pipeline, claim, _posting(), charges=_Ledger(), detail=detail)

        assert harness.holds.list_open() == []


class TestTheSameRemittanceTwice:
    def test_redelivery_leaves_one_hold(self, harness) -> None:
        """A vendor replaying an 835 is expected, and is one disagreement."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )
        posting = _posting()

        stored, _ = apply_posting(
            harness.pipeline, claim, posting, charges=_Ledger(), detail=detail
        )
        apply_posting(harness.pipeline, stored, posting, charges=_Ledger(), detail=detail)

        assert len(harness.holds.list_open()) == 1

    def test_a_second_disagreement_on_the_same_claim_does_not_stack(self, harness) -> None:
        """A secondary payer disagreeing while the first hold is open is the
        same conversation with the practice, not a new one."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )
        stored, _ = apply_posting(
            harness.pipeline, claim, _posting(), charges=_Ledger(), detail=detail
        )
        second = _posting(paid=12_500, responsibility=2_500)

        apply_posting(harness.pipeline, stored, second, charges=_Ledger(), detail=detail)

        assert len(harness.holds.list_open()) >= 1
        assert harness.holds.open_for_claim(claim.id) is not None


class TestWithholdingDoesNotDependOnRecording:
    def test_a_pipeline_with_no_hold_repository_still_refuses_to_bill(self, harness) -> None:
        """The safety property must not be conditional on its own backstop.

        A deployment that never configured a hold repository is exactly the
        one where a quietly-wrong bill would go unnoticed longest.
        """
        harness.pipeline.holds = None
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )

        apply_posting(harness.pipeline, claim, _posting(), charges=ledger, detail=detail)

        assert ledger.rows == []

    def test_and_says_out_loud_that_it_could_not_write_the_hold_down(
        self, harness, caplog: pytest.LogCaptureFixture
    ) -> None:
        harness.pipeline.holds = None
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )

        with caplog.at_level("WARNING"):
            apply_posting(harness.pipeline, claim, _posting(), charges=_Ledger(), detail=detail)

        assert "remittance_hold_unrecorded" in caplog.text


class TestTheHoldIsRaisedEvenWhenThisCallerCannotBill:
    def test_the_webhook_path_raises_the_hold_it_found(self, harness) -> None:
        """The webhook passes no ledger and is usually first to see the 835.

        If the check only ran where a ledger row was about to be written,
        the path that actually holds the document would never raise a hold,
        and the path that raises holds would already have been deduped out
        by the receipt ledger.
        """
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _detail(
            claim.control_number,
            line_adjustments=[_adjustment("CO", "45", 3_000)],
            stated_responsibility=3_000,
        )

        apply_posting(harness.pipeline, claim, _posting(), charges=None, detail=detail)

        assert len(harness.holds.list_open()) == 1


class TestWhatTheHoldRemembers:
    def test_the_withheld_row_is_computed_against_the_ledger_not_stored(self) -> None:
        """A stored "we withheld $30" goes stale the moment anything else
        touches the ledger; the difference is what actually gets written."""
        hold = _a_hold(patient_responsibility_cents=3_000)

        assert withheld_cents(hold, already_billed=0) == 3_000
        assert withheld_cents(hold, already_billed=1_000) == 2_000

    def test_a_secondary_payer_leaves_a_credit_rather_than_a_charge(self) -> None:
        """Negative is correct here and must not be clamped: the primary
        billed the client and the secondary paid it off."""
        hold = _a_hold(patient_responsibility_cents=0)

        assert withheld_cents(hold, already_billed=3_000) == -3_000

    def test_codes_are_read_claim_level_first_then_line_level(self) -> None:
        detail = _detail("CLM1", line_adjustments=[_adjustment("PR", "2", 3_000)])
        detail = detail.model_copy(update={"adjustments": [_adjustment("PI", "137", 0)]})

        assert codes_of(detail) == [
            {"group_code": "PI", "reason_code": "137"},
            {"group_code": "PR", "reason_code": "2"},
        ]


class TestTheRepositoryRefusesADuplicate:
    def test_two_holds_on_one_posting_key_is_an_error(self) -> None:
        repo = InMemoryRemittanceHoldRepository()
        hold = _a_hold()
        repo.add(hold)

        with pytest.raises(ValueError, match="already recorded"):
            repo.add(hold.model_copy(update={"id": "second"}))

    def test_recording_a_duplicate_is_not_an_error_for_the_posting_path(self) -> None:
        """A replayed 835 must not take the posting down with it."""
        repo = InMemoryRemittanceHoldRepository()
        hold = _a_hold()
        repo.add(hold)

        assert record(repo, hold.model_copy(update={"id": "second"})) is None
        assert len(repo.list_open()) == 1

    def test_acknowledging_keeps_the_hold_open(self) -> None:
        """Somebody saying they have seen it is not somebody deciding."""
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_a_hold())

        acknowledged = repo.acknowledge(hold.id, at=_AT)

        assert acknowledged is not None
        assert acknowledged.state == "acknowledged"
        assert acknowledged.is_open
        assert len(repo.list_open()) == 1

    def test_acknowledging_twice_keeps_the_first_moment(self) -> None:
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_a_hold())
        repo.acknowledge(hold.id, at=_AT)
        later = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

        again = repo.acknowledge(hold.id, at=later)

        assert again is not None
        assert again.acknowledged_at == _AT

    def test_resolving_closes_it_and_names_who_decided(self) -> None:
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_a_hold())

        resolved = repo.resolve(hold.id, finding="waived", user_id="u1", at=_AT)

        assert resolved is not None
        assert resolved.state == "resolved"
        assert resolved.finding == "waived"
        assert resolved.resolved_by_user_id == "u1"
        assert not resolved.is_open
        assert repo.list_open() == []

    def test_a_second_decision_does_not_overwrite_the_first(self) -> None:
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_a_hold())
        repo.resolve(hold.id, finding="waived", user_id="u1", at=_AT)

        again = repo.resolve(hold.id, finding="bill_as_stated", user_id="u2", at=_AT)

        assert again is not None
        assert again.finding == "waived"
        assert again.resolved_by_user_id == "u1"

    def test_nothing_releases_a_hold_but_a_decision(self) -> None:
        """There is no timeout, no expiry and no auto-approve to call."""
        repo = InMemoryRemittanceHoldRepository()
        repo.add(_a_hold())

        assert not hasattr(repo, "expire")
        assert not hasattr(repo, "release")
        assert len(repo.list_open()) == 1


def _a_hold(**overrides):
    fields = {
        "id": "hold-1",
        "claim_id": "claim-1",
        "patient_id": "patient-1",
        "control_number": "CLM1",
        "posting_key": "claim-1:835:txn:CLM1",
        "reason": "patient_responsibility",
        "stated_cents": 3_000,
        "computed_cents": 0,
        "patient_responsibility_cents": 3_000,
        "detected_at": _AT,
    }
    return RemittanceHold(**{**fields, **overrides})
