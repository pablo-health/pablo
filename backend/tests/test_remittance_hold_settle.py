# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A practice settling a held remittance itself.

A hold that nobody sees is a client whose balance quietly stopped being
billed, so the disagreement has to reach the clinician who owns the claim
as work — and they have to be able to answer it without waiting on anyone.

Two answers, both available for the whole life of the hold: bill the amount
the payer stated, or waive it. The tests that matter most here are the ones
asserting what is NOT required — no acknowledgement first, no expiry, no
second party — because every one of those would be the software putting
itself between a practice and its own client's balance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.claims.events import (
    ClaimEvent,
    clear_claim_event_listeners,
    compliance_item_type,
    compliance_reminder_listener,
    register_claim_event_listener,
)
from app.claims.holds import settle
from app.claims.remittance import apply_posting, posting_for
from app.compliance.templates import _TEMPLATES
from app.models.claims_holds import RemittanceHold
from app.models.claims_responses import Adjustment, RemittanceClaim, RemittanceLine
from app.models.claims_timeline import ClaimTimeline, TimelinePayment
from app.repositories.remittance_hold import InMemoryRemittanceHoldRepository
from app.routes.claims import router

from .claims_pipeline_fakes import make_harness, restore_listeners

CHARGED = 15_000
_AT = datetime(2026, 9, 8, 12, 0, tzinfo=UTC)


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


def _hold(**overrides) -> RemittanceHold:
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


def _repo_with(hold: RemittanceHold) -> InMemoryRemittanceHoldRepository:
    repo = InMemoryRemittanceHoldRepository()
    repo.add(hold)
    return repo


class TestBillingItAsStated:
    def test_it_writes_exactly_the_row_that_was_withheld(self) -> None:
        repo = _repo_with(_hold())
        ledger = _Ledger()

        settle(
            repo,
            _hold(),
            finding="bill_as_stated",
            charges=ledger,
            already_billed=0,
            user_id="u1",
            now=_AT,
        )

        assert len(ledger.rows) == 1
        row = ledger.rows[0]
        assert row["amount_cents"] == 3_000
        assert row["kind"] == "patient_resp"
        assert row["claim_id"] == "claim-1"
        assert row["patient_id"] == "patient-1"
        assert row["user_id"] == "u1"

    def test_the_row_is_the_difference_from_what_the_ledger_already_carries(self) -> None:
        """Recomputed at the moment of billing, not read from the hold.

        Between the hold being raised and somebody deciding, the practice
        may have billed part of this claim by hand. Writing the stated
        total would bill that part twice.
        """
        repo = _repo_with(_hold())
        ledger = _Ledger()

        settle(
            repo,
            _hold(),
            finding="bill_as_stated",
            charges=ledger,
            already_billed=1_000,
            user_id="u1",
            now=_AT,
        )

        assert ledger.rows[0]["amount_cents"] == 2_000

    def test_nothing_left_to_bill_writes_no_row(self) -> None:
        repo = _repo_with(_hold())
        ledger = _Ledger()

        settle(
            repo,
            _hold(),
            finding="bill_as_stated",
            charges=ledger,
            already_billed=3_000,
            user_id="u1",
            now=_AT,
        )

        assert ledger.rows == []

    def test_the_hold_closes_naming_who_decided_and_how(self) -> None:
        repo = _repo_with(_hold())

        resolved = settle(
            repo,
            _hold(),
            finding="bill_as_stated",
            charges=_Ledger(),
            already_billed=0,
            user_id="u1",
            now=_AT,
        )

        assert resolved is not None
        assert resolved.state == "resolved"
        assert resolved.finding == "bill_as_stated"
        assert resolved.resolved_by_user_id == "u1"
        assert resolved.resolved_at == _AT
        assert repo.list_open() == []

    def test_billing_with_no_ledger_raises_rather_than_closing_the_hold(self) -> None:
        """Closing a hold with no bill behind it is the worst outcome here:
        the client is never billed and the record says the practice chose
        to bill them."""
        repo = _repo_with(_hold())

        with pytest.raises(ValueError, match="charge ledger"):
            settle(
                repo,
                _hold(),
                finding="bill_as_stated",
                charges=None,
                already_billed=0,
                user_id="u1",
                now=_AT,
            )

        assert len(repo.list_open()) == 1


class TestWaivingIt:
    def test_it_writes_no_ledger_row(self) -> None:
        repo = _repo_with(_hold())
        ledger = _Ledger()

        settle(
            repo,
            _hold(),
            finding="waived",
            charges=ledger,
            already_billed=0,
            user_id="u1",
            now=_AT,
        )

        assert ledger.rows == []

    def test_it_closes_the_hold(self) -> None:
        repo = _repo_with(_hold())

        resolved = settle(
            repo,
            _hold(),
            finding="waived",
            charges=None,
            already_billed=0,
            user_id="u1",
            now=_AT,
        )

        assert resolved is not None
        assert resolved.finding == "waived"
        assert repo.list_open() == []

    def test_a_diagnostic_finding_also_bills_nothing(self) -> None:
        """``parse_error`` and ``payer_inconsistent`` say why the numbers
        disagreed. Neither is a decision to bill somebody."""
        for finding in ("parse_error", "payer_inconsistent"):
            repo = _repo_with(_hold())
            ledger = _Ledger()

            settle(
                repo,
                _hold(),
                finding=finding,
                charges=ledger,
                already_billed=0,
                user_id="u1",
                now=_AT,
            )

            assert ledger.rows == [], finding


class TestNothingMakesThePracticeWait:
    def test_both_answers_work_on_a_hold_nobody_acknowledged(self) -> None:
        for finding in ("bill_as_stated", "waived"):
            repo = _repo_with(_hold())

            resolved = settle(
                repo,
                _hold(),
                finding=finding,
                charges=_Ledger(),
                already_billed=0,
                user_id="u1",
                now=_AT,
            )

            assert resolved is not None, finding
            assert resolved.finding == finding

    def test_both_answers_work_on_a_hold_acknowledged_long_ago(self) -> None:
        repo = _repo_with(_hold())
        repo.acknowledge("hold-1", at=_AT)
        much_later = datetime(2027, 3, 1, tzinfo=UTC)

        resolved = settle(
            repo,
            repo.get("hold-1"),
            finding="bill_as_stated",
            charges=_Ledger(),
            already_billed=0,
            user_id="u1",
            now=much_later,
        )

        assert resolved is not None
        assert resolved.state == "resolved"

    def test_acknowledging_does_not_decide_anything(self) -> None:
        """It silences the short re-notification tier and nothing else: no
        ledger row, and the bill still waits for a person."""
        repo = _repo_with(_hold())
        ledger = _Ledger()

        repo.acknowledge("hold-1", at=_AT)

        assert ledger.rows == []
        assert len(repo.list_open()) == 1


class TestTheHoldReachesTheClinicianAsWork:
    def test_a_hold_writes_a_reminder_for_the_clinician_who_owns_the_claim(self, harness) -> None:
        """Through the surface a rejection or a denial already uses, not a
        notification channel of this feature's own."""
        clear_claim_event_listeners()
        seen: list[ClaimEvent] = []
        register_claim_event_listener(lambda _session, event: seen.append(event))
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)

        apply_posting(
            harness.pipeline,
            claim,
            _posting(),
            charges=_Ledger(),
            detail=_disagreeing(claim.control_number),
        )

        [event] = [e for e in seen if e.kind == "remittance_held"]
        assert event.claim_id == claim.id
        assert event.control_number == claim.control_number
        assert event.user_id == harness.pipeline.principal_user_id

    def test_the_reminder_names_the_claim_and_the_payer(self, harness) -> None:
        clear_claim_event_listeners()
        seen: list[ClaimEvent] = []
        register_claim_event_listener(lambda _session, event: seen.append(event))
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)

        apply_posting(
            harness.pipeline,
            claim,
            _posting(),
            charges=_Ledger(),
            detail=_disagreeing(claim.control_number),
        )

        [event] = [e for e in seen if e.kind == "remittance_held"]
        assert event.payer_name == claim.subscriber_snapshot.payer_name

    def test_an_agreeing_remittance_announces_nothing(self, harness) -> None:
        clear_claim_event_listeners()
        seen: list[ClaimEvent] = []
        register_claim_event_listener(lambda _session, event: seen.append(event))
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)

        apply_posting(
            harness.pipeline,
            claim,
            _posting(),
            charges=_Ledger(),
            detail=_agreeing(claim.control_number),
        )

        assert [e for e in seen if e.kind == "remittance_held"] == []

    def test_a_redelivered_remittance_does_not_nag_twice(self, harness) -> None:
        """A duplicate hold is not written, so no second reminder either."""
        clear_claim_event_listeners()
        seen: list[ClaimEvent] = []
        register_claim_event_listener(lambda _session, event: seen.append(event))
        claim = harness.add(state="payer_accepted", total_charge_cents=CHARGED)
        posting = _posting()
        detail = _disagreeing(claim.control_number)

        stored, _ = apply_posting(
            harness.pipeline, claim, posting, charges=_Ledger(), detail=detail
        )
        apply_posting(harness.pipeline, stored, posting, charges=_Ledger(), detail=detail)

        assert len([e for e in seen if e.kind == "remittance_held"]) == 1

    def test_the_event_kind_has_a_compliance_template_to_land_in(self) -> None:
        """A kind with no template writes a reminder the dashboard cannot
        render, which is a reminder nobody sees."""
        item_type = compliance_item_type("remittance_held")

        assert item_type == "claim_remittance_held"
        assert any(t.item_type == item_type for t in _TEMPLATES)

    def test_the_default_listener_writes_one_reminder_per_hold(self) -> None:
        """Guards the wording too: a reminder that does not say the client
        was not billed is a reminder somebody deprioritises."""
        clear_claim_event_listeners()
        register_claim_event_listener(compliance_reminder_listener)
        added: list[object] = []
        session = SimpleNamespace(
            add=added.append,
            flush=lambda: None,
            execute=lambda _q: SimpleNamespace(scalar_one_or_none=lambda: None),
        )
        event = ClaimEvent(
            kind="remittance_held",
            control_number="CLM1",
            claim_id="claim-1",
            user_id="u1",
            payer_id="p1",
            payer_name="Aetna",
            state="partial",
            occurred_at=_AT,
        )

        compliance_reminder_listener(session, event)

        [row] = added
        assert row.item_type == "claim_remittance_held"
        assert "Aetna" in row.label
        assert "not billed" in row.label
        assert "CLM1" in row.notes


class TestTheRoutesAreReachable:
    def test_holds_is_declared_above_the_claim_id_catch_all(self) -> None:
        """The bug this guards is silent and total.

        FastAPI matches routes in registration order, so ``/api/claims/holds``
        declared after ``/api/claims/{claim_id}`` never matches — every
        request arrives at the claim detail route as ``claim_id="holds"``
        and answers 404. Nothing about the code looks wrong; the surface is
        just gone.
        """
        paths = [getattr(route, "path", "") for route in router.routes]

        assert paths.index("/api/claims/holds") < paths.index("/api/claims/{claim_id}")

    def test_every_hold_route_exists(self) -> None:
        declared = {
            (getattr(route, "path", ""), frozenset(getattr(route, "methods", ())))
            for route in router.routes
        }

        assert ("/api/claims/holds", frozenset({"GET"})) in declared
        assert ("/api/claims/holds/{hold_id}/resolve", frozenset({"POST"})) in declared
        assert ("/api/claims/holds/{hold_id}/acknowledge", frozenset({"POST"})) in declared


# --- fixtures for the posting-path tests above -----------------------------


def _adjustment(group: str, reason: str, cents: int) -> Adjustment:
    return Adjustment(group_code=group, reason_code=reason, amount_cents=cents)


def _remittance(control_number: str, *, line_adjustments, stated: int) -> RemittanceClaim:
    return RemittanceClaim(
        patient_control_number=control_number,
        payer_claim_control_number="P1",
        claim_status_code="1",
        total_charge_cents=CHARGED,
        paid_cents=12_000,
        patient_responsibility_cents=stated,
        claim_frequency_code="1",
        adjustments=[],
        lines=[
            RemittanceLine(
                line_control_number=f"{control_number}L1",
                service_date="20260901",
                cpt="90837",
                charge_cents=CHARGED,
                paid_cents=12_000,
                adjustments=line_adjustments,
            )
        ],
    )


def _disagreeing(control_number: str) -> RemittanceClaim:
    """Stated as the client's $30; itemised as a contractual write-off."""
    return _remittance(
        control_number, line_adjustments=[_adjustment("CO", "45", 3_000)], stated=3_000
    )


def _agreeing(control_number: str) -> RemittanceClaim:
    return _remittance(
        control_number, line_adjustments=[_adjustment("PR", "2", 3_000)], stated=3_000
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
