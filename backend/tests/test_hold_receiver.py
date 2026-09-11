# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The seam that hands a held remittance to whoever will investigate it.

Almost every test here is a variation on one claim: **the engine's
behaviour does not depend on the receiver.** A receiver that raises, or was
never registered, must leave the client unbilled, the hold open, the
practice asked and the posting written — exactly as if it had worked.

That is the opposite of the usual advice about swallowing exceptions, and
it is right here for a specific reason: by the time this is called the
safe decision has already been made and written down. The receiver is an
observer. If it breaks, the investigation is delayed and nothing else is.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.claims.hold_receiver import (
    HeldRemittance,
    NoopRemittanceHoldReceiver,
    get_remittance_hold_receiver,
    hand_over,
    register_remittance_hold_receiver,
    vendor_transaction_of,
)
from app.claims.holds import settle
from app.claims.remittance import apply_posting, posting_for
from app.models.claims_holds import RemittanceHold
from app.models.claims_responses import Adjustment, RemittanceClaim, RemittanceLine
from app.models.claims_timeline import ClaimTimeline, TimelinePayment
from app.repositories.remittance_hold import InMemoryRemittanceHoldRepository

from .claims_pipeline_fakes import make_harness, restore_listeners

CHARGED = 15_000
_AT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


class _Recording:
    def __init__(self) -> None:
        self.seen: list[HeldRemittance] = []

    def receive(self, held: HeldRemittance) -> None:
        self.seen.append(held)


class _Exploding:
    def receive(self, held: HeldRemittance) -> None:
        msg = "the investigation service is down"
        raise RuntimeError(msg)


@pytest.fixture(autouse=True)
def _restore_receiver():
    yield
    register_remittance_hold_receiver(None)


@pytest.fixture
def harness():
    made = make_harness()
    yield made
    restore_listeners()


class _Ledger:
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


def _disagreeing(control_number: str) -> RemittanceClaim:
    return RemittanceClaim(
        patient_control_number=control_number,
        payer_claim_control_number="P1",
        claim_status_code="1",
        total_charge_cents=CHARGED,
        paid_cents=12_000,
        patient_responsibility_cents=3_000,
        claim_frequency_code="1",
        adjustments=[],
        lines=[
            RemittanceLine(
                line_control_number=f"{control_number}L1",
                service_date="20260901",
                cpt="90837",
                charge_cents=CHARGED,
                paid_cents=12_000,
                adjustments=[_adjustment("CO", "45", 3_000)],
            )
        ],
    )


def _posting():
    timeline = ClaimTimeline(
        payments=[
            TimelinePayment(
                id="clp_1",
                disposition="paid",
                charged_cents=CHARGED,
                paid_cents=12_000,
                patient_responsibility_cents=3_000,
                trace_number="EFT1",
                processed_at=_AT,
            )
        ]
    )
    posting = posting_for(timeline, charged_cents=CHARGED)
    assert posting is not None
    return posting


def _hold(**overrides) -> RemittanceHold:
    fields = {
        "id": "hold-1",
        "claim_id": "claim-1",
        "patient_id": "patient-1",
        "control_number": "CLM0001",
        "posting_key": "claim-1:835:txn_abc:CLM0001",
        "reason": "patient_responsibility",
        "stated_cents": 3_000,
        "computed_cents": 0,
        "patient_responsibility_cents": 3_000,
        "detected_at": _AT,
    }
    return RemittanceHold(**{**fields, **overrides})


class TestTheDefaultIsNothing:
    def test_a_deployment_that_registers_nothing_gets_the_noop(self) -> None:
        assert isinstance(get_remittance_hold_receiver(), NoopRemittanceHoldReceiver)

    def test_the_noop_is_the_ordinary_mode_not_a_degraded_one(self, harness) -> None:
        """A self-hoster who never heard of this still gets the whole loop."""
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()

        apply_posting(
            harness.pipeline,
            claim,
            _posting(),
            charges=ledger,
            detail=_disagreeing(claim.control_number),
        )

        assert ledger.rows == []
        assert len(harness.holds.list_open()) == 1

    def test_registering_none_restores_the_default(self) -> None:
        register_remittance_hold_receiver(_Recording())
        register_remittance_hold_receiver(None)

        assert isinstance(get_remittance_hold_receiver(), NoopRemittanceHoldReceiver)


class TestWhatCrossesTheSeam:
    def test_a_raised_hold_arrives_with_the_remittance_behind_it(self, harness) -> None:
        """The parser's own reading is the half that is nowhere else, and it
        is the half somebody investigating has to compare against."""
        receiver = _Recording()
        register_remittance_hold_receiver(receiver)
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        detail = _disagreeing(claim.control_number)

        apply_posting(harness.pipeline, claim, _posting(), charges=_Ledger(), detail=detail)

        [held] = [h for h in receiver.seen if h.transition == "raised"]
        assert held.hold.claim_id == claim.id
        assert held.remittance is not None
        assert held.remittance.patient_control_number == claim.control_number

    def test_a_resolution_crosses_too_so_a_receiver_need_not_poll(self) -> None:
        receiver = _Recording()
        register_remittance_hold_receiver(receiver)
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_hold())

        settle(
            repo,
            hold,
            finding="waived",
            charges=None,
            already_billed=0,
            user_id="u1",
            now=_AT,
        )

        [held] = [h for h in receiver.seen if h.transition == "resolved"]
        assert held.hold.finding == "waived"

    def test_a_transition_hands_over_no_remittance(self) -> None:
        """The engine keeps no second copy; by now the document is long out
        of hand, and inventing one would be a lie about provenance."""
        receiver = _Recording()
        register_remittance_hold_receiver(receiver)
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_hold())

        settle(
            repo,
            hold,
            finding="waived",
            charges=None,
            already_billed=0,
            user_id="u1",
            now=_AT,
        )

        [held] = [h for h in receiver.seen if h.transition == "resolved"]
        assert held.remittance is None


class TestFindingTheDocumentAgain:
    def test_a_webhook_hold_names_the_vendor_transaction(self) -> None:
        """Which is how the original file is found in the vendor's records."""
        assert vendor_transaction_of("claim-1:835:txn_abc:CLM0001") == "txn_abc"

    def test_a_timeline_hold_names_none_rather_than_guessing(self) -> None:
        """That adjudication was read from an API, not delivered as a
        document, so there is no transaction to go and find. Returning
        something plausible would send an investigator looking for a file
        that does not exist."""
        assert vendor_transaction_of("claim-1:clp_1") is None

    def test_it_reaches_the_receiver(self) -> None:
        receiver = _Recording()
        register_remittance_hold_receiver(receiver)

        hand_over(_hold(), "raised")

        assert receiver.seen[0].vendor_transaction_id == "txn_abc"


class TestABrokenReceiverChangesNothing:
    def test_the_client_is_still_not_billed(self, harness) -> None:
        register_remittance_hold_receiver(_Exploding())
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        ledger = _Ledger()

        apply_posting(
            harness.pipeline,
            claim,
            _posting(),
            charges=ledger,
            detail=_disagreeing(claim.control_number),
        )

        assert ledger.rows == []

    def test_the_hold_is_still_written_and_still_open(self, harness) -> None:
        register_remittance_hold_receiver(_Exploding())
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)

        apply_posting(
            harness.pipeline,
            claim,
            _posting(),
            charges=_Ledger(),
            detail=_disagreeing(claim.control_number),
        )

        assert len(harness.holds.list_open()) == 1

    def test_the_payers_payment_still_posts(self, harness) -> None:
        register_remittance_hold_receiver(_Exploding())
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)

        stored, moved = apply_posting(
            harness.pipeline,
            claim,
            _posting(),
            charges=_Ledger(),
            detail=_disagreeing(claim.control_number),
        )

        assert moved is True
        assert stored.total_paid_cents == 12_000

    def test_a_resolution_still_resolves(self) -> None:
        register_remittance_hold_receiver(_Exploding())
        repo = InMemoryRemittanceHoldRepository()
        hold = repo.add(_hold())

        resolved = settle(
            repo,
            hold,
            finding="waived",
            charges=None,
            already_billed=0,
            user_id="u1",
            now=_AT,
        )

        assert resolved is not None
        assert resolved.state == "resolved"

    def test_the_failure_is_logged_rather_than_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Swallowed is not the same as unreported."""
        register_remittance_hold_receiver(_Exploding())

        with caplog.at_level(logging.WARNING):
            hand_over(_hold(), "raised")

        assert "remittance_hold_receiver_failed" in caplog.text
        assert "_Exploding" in caplog.text
