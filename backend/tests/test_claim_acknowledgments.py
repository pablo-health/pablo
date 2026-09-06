# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Applying a 277CA to a claim (``app.claims.acknowledgments``).

The rows of the state table an acknowledgement drives — submitted or
ch_accepted + payer accept -> payer_accepted, any of those + reject ->
rejected, submitted + clearinghouse accept -> ch_accepted, stalled + a
late receipt -> wherever it says — and the idempotency both delivery
paths rely on: the same transaction, or the same vendor event id, applied
twice moves nothing the second time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.claims.acknowledgments import (
    Provenance,
    apply_acknowledgment,
    apply_fetched,
    fetch_acknowledgment,
)
from app.claims.clearinghouse import ClearinghouseNotFoundError
from app.claims.responses import parse_277

from tests.claims_pipeline_fakes import (
    NOW,
    PipelineHarness,
    acknowledgment_report,
    make_harness,
    restore_listeners,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.claims.acknowledgments import AcknowledgmentOutcome
    from app.models.claims import Claim

    from tests.claims_pipeline_fakes import AcknowledgmentKind

_PROCESSED_AT = datetime(2026, 9, 6, 15, 51, 22, tzinfo=UTC)


@pytest.fixture
def harness() -> Iterator[PipelineHarness]:
    built = make_harness()
    yield built
    restore_listeners()


def _apply(
    harness: PipelineHarness,
    claim: Claim,
    kind: AcknowledgmentKind,
    *,
    transaction_id: str = "tx-1",
    vendor_event_id: str | None = None,
) -> tuple[AcknowledgmentOutcome, Claim | None]:
    [ack] = parse_277(
        acknowledgment_report(kind, claim.control_number, transaction_id=transaction_id)
    )
    return apply_acknowledgment(
        harness.pipeline,
        ack,
        Provenance(
            transaction_id=transaction_id,
            vendor_event_id=vendor_event_id,
            occurred_at=_PROCESSED_AT,
        ),
    )


# --- the accepting rows ---------------------------------------------------------


def test_the_clearinghouse_forwarding_moves_submitted_to_ch_accepted(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="submitted", submitted_at=NOW)

    outcome, moved = _apply(harness, created, "clearinghouse_forwarded")

    assert outcome == "moved"
    assert moved is not None
    assert moved.state == "ch_accepted"
    assert moved.last_receipt_at == _PROCESSED_AT
    assert moved.payer_accepted_at is None
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "ch_accepted"
    assert receipt.vendor_transaction_id == "tx-1"
    assert receipt.occurred_at == _PROCESSED_AT
    assert receipt.detail["source"] == "clearinghouse"
    assert receipt.detail["codes"] == [{"system": "status", "code": "A1:16"}]
    assert harness.listener.events == []


@pytest.mark.parametrize("state", ["submitted", "ch_accepted", "stalled"])
def test_the_payer_accepting_moves_the_claim_to_payer_accepted(
    harness: PipelineHarness, state: str
) -> None:
    created = harness.add(state=state, submitted_at=NOW)

    outcome, moved = _apply(harness, created, "payer_accepted")

    assert outcome == "moved"
    assert moved is not None
    assert moved.state == "payer_accepted"
    assert moved.payer_accepted_at == NOW
    assert moved.payer_claim_number == "PYR2026090600001"
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert (receipt.kind, receipt.from_state, receipt.to_state) == (
        "payer_accepted",
        state,
        "payer_accepted",
    )


def test_a_clearinghouse_receipt_after_the_payer_spoke_is_recorded_not_applied(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="payer_accepted", submitted_at=NOW, payer_accepted_at=NOW)

    outcome, same = _apply(harness, created, "clearinghouse_forwarded")

    assert outcome == "recorded"
    assert same is not None
    assert same.state == "payer_accepted"
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "acknowledged"
    assert receipt.to_state == "payer_accepted"


def test_a_stalled_claim_that_the_payer_had_accepted_does_not_step_back(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="stalled", submitted_at=NOW, payer_accepted_at=NOW)

    outcome, same = _apply(harness, created, "clearinghouse_forwarded")

    assert outcome == "recorded"
    assert same is not None
    assert same.state == "stalled"


# --- the rejecting rows ---------------------------------------------------------


@pytest.mark.parametrize("state", ["submitted", "ch_accepted", "payer_accepted", "stalled"])
def test_the_payer_rejecting_moves_the_claim_to_rejected_with_the_status_codes(
    harness: PipelineHarness, state: str
) -> None:
    created = harness.add(state=state, submitted_at=NOW)

    outcome, moved = _apply(harness, created, "payer_rejected")

    assert outcome == "moved"
    assert moved is not None
    assert moved.state == "rejected"
    assert [(f.source, f.code) for f in moved.submission_findings] == [
        ("status", "A7:21"),
        ("status", "A7:164"),
    ]
    assert moved.submission_findings[0].description == "Missing or invalid information."
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "rejected"
    [event] = harness.listener.events
    assert event.kind == "rejected"
    assert [(c.system, c.code, c.description) for c in event.detail.codes] == [
        ("status", "A7:21", None),
        ("status", "A7:164", None),
    ]
    flat = str(event.to_dict())
    for forbidden in ("123456789", "Anon", "F41", "2000-01-01"):
        assert forbidden not in flat


def test_a_rejection_of_a_finished_claim_is_recorded_only(harness: PipelineHarness) -> None:
    created = harness.add(state="paid", submitted_at=NOW, adjudicated_at=NOW)

    outcome, same = _apply(harness, created, "payer_rejected")

    assert outcome == "recorded"
    assert same is not None
    assert same.state == "paid"
    assert harness.listener.events == []


# --- idempotency ---------------------------------------------------------------


def test_the_same_transaction_applied_twice_moves_nothing_the_second_time(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="submitted", submitted_at=NOW)

    first, _ = _apply(harness, created, "payer_accepted", transaction_id="tx-9")
    second, _ = _apply(harness, created, "payer_accepted", transaction_id="tx-9")

    assert (first, second) == ("moved", "duplicate")
    assert len(harness.receipts.list_for_claim(created.id)) == 1
    assert harness.get(created.id).state == "payer_accepted"


def test_a_redelivered_vendor_event_is_a_no_op(harness: PipelineHarness) -> None:
    created = harness.add(state="submitted", submitted_at=NOW)

    first, _ = _apply(
        harness, created, "payer_rejected", transaction_id="tx-a", vendor_event_id="evt-1"
    )
    second, _ = _apply(
        harness, created, "payer_rejected", transaction_id="tx-b", vendor_event_id="evt-1"
    )

    assert (first, second) == ("moved", "duplicate")
    assert len(harness.receipts.list_for_claim(created.id)) == 1
    assert harness.listener.kinds() == ["rejected"]


def test_a_control_number_nobody_filed_is_unmatched(harness: PipelineHarness) -> None:
    [ack] = parse_277(acknowledgment_report("payer_accepted", "NOTOURS", transaction_id="t"))

    outcome, claim = apply_acknowledgment(harness.pipeline, ack, Provenance(transaction_id="t"))

    assert (outcome, claim) == ("unmatched", None)


def test_control_numbers_match_regardless_of_case(harness: PipelineHarness) -> None:
    created = harness.add(state="submitted", submitted_at=NOW, control_number="AbC123")
    [ack] = parse_277(acknowledgment_report("payer_accepted", "ABC123", transaction_id="t"))

    outcome, _ = apply_acknowledgment(harness.pipeline, ack, Provenance(transaction_id="t"))

    assert outcome == "moved"
    assert harness.get(created.id).state == "payer_accepted"


# --- fetching ------------------------------------------------------------------


def test_fetch_reads_the_report_behind_an_inbound_277(harness: PipelineHarness) -> None:
    created = harness.add(state="submitted", submitted_at=NOW)
    transaction = harness.client.acknowledge("payer_accepted", created.control_number)

    fetched = fetch_acknowledgment(harness.client, transaction)

    assert fetched is not None
    assert fetched.transaction_id == transaction
    assert fetched.processed_at == NOW
    assert fetched.control_numbers == {created.control_number.upper()}
    [(outcome, moved)] = apply_fetched(harness.pipeline, fetched, vendor_event_id="evt-7")
    assert outcome == "moved"
    assert moved is not None
    assert moved.state == "payer_accepted"
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.vendor_event_id == "evt-7"


def test_fetch_ignores_a_transaction_that_is_not_an_inbound_277(
    harness: PipelineHarness,
) -> None:
    transaction = harness.client.filed("ANY")

    assert fetch_acknowledgment(harness.client, transaction) is None


def test_fetch_raises_for_a_transaction_this_account_does_not_own(
    harness: PipelineHarness,
) -> None:
    with pytest.raises(ClearinghouseNotFoundError):
        fetch_acknowledgment(harness.client, "someone-elses")
