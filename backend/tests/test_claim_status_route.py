# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The on-demand status check (``app.routes.claim_status``), and what the
detail view and the tracker show of the pipeline's work: receipts, the
clearinghouse hop's moment, the findings behind a rejection, the next
action.

Built on the same in-memory harness as the claim routes, with the status
router mounted beside them and the clearinghouse answered by the fake.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from app.claims.acknowledgments import Provenance, apply_acknowledgment
from app.claims.receipts import ClaimPipeline
from app.claims.responses import parse_277
from app.db import get_db_session
from app.repositories import get_claim_receipt_repository
from app.routes import claim_status
from app.routes.coverage import get_clearinghouse_client

from tests.claims_pipeline_fakes import (
    NOW,
    FakeClearinghouse,
    acknowledgment_report,
    restore_listeners,
)
from tests.test_claims_routes import _USER_ID, _audit_rows, _build, _validated, harness

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["harness"]


@pytest.fixture
def tracker(harness: dict[str, Any]) -> Iterator[dict[str, Any]]:
    app = harness["app"]
    app.include_router(claim_status.router)
    client = FakeClearinghouse()
    receipts = app.dependency_overrides[get_claim_receipt_repository]()
    app.dependency_overrides[get_clearinghouse_client] = lambda: client
    app.dependency_overrides[get_db_session] = object
    yield {**harness, "clearinghouse": client, "receipts": receipts}
    restore_listeners()


def _submitted(tracker: dict[str, Any]) -> dict[str, Any]:
    """A validated claim moved to submitted two hours ago, as the outbox would leave it."""
    validated = tracker["claims"].get(_validated(tracker)["id"])
    moved = validated.model_copy(
        update={
            "state": "submitted",
            "submitted_at": NOW - timedelta(hours=2),
            "vendor_claim_id": "01MVENDOR",
        }
    )
    saved = tracker["claims"].update(moved)
    return dict(saved.model_dump())


def _pipeline(tracker: dict[str, Any]) -> ClaimPipeline:
    return ClaimPipeline(
        claims=tracker["claims"],
        receipts=tracker["receipts"],
        session=object(),  # type: ignore[arg-type]
        principal_user_id=_USER_ID,
        now=lambda: NOW,
    )


def _acknowledge(tracker: dict[str, Any], claim: dict[str, Any], kind: Any) -> None:
    [ack] = parse_277(acknowledgment_report(kind, claim["control_number"], transaction_id="t"))
    apply_acknowledgment(_pipeline(tracker), ack, Provenance(transaction_id="t", occurred_at=NOW))


# --- the detail view and the tracker ---------------------------------------------


def test_the_tracker_and_the_detail_say_what_to_do_next(tracker: dict[str, Any]) -> None:
    draft = _build(tracker)
    queued = _validated(tracker)

    rows = {row["id"]: row for row in tracker["client"].get("/api/claims").json()["data"]}

    assert rows[draft["id"]]["next_action"] == "review_and_file"
    assert rows[queued["id"]]["next_action"] == "queued_to_send"
    assert rows[queued["id"]]["last_receipt_at"] is None
    detail = tracker["client"].get(f"/api/claims/{queued['id']}").json()
    assert detail["next_action"] == "queued_to_send"
    assert detail["receipts"] == []
    assert detail["submission_findings"] == []


def test_the_detail_carries_the_receipts_and_the_findings_behind_a_rejection(
    tracker: dict[str, Any],
) -> None:
    submitted = _submitted(tracker)
    _acknowledge(tracker, submitted, "payer_rejected")

    resp = tracker["client"].get(f"/api/claims/{submitted['id']}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "rejected"
    assert body["next_action"] == "correct_and_resubmit"
    assert [f["code"] for f in body["submission_findings"]] == ["A7:21", "A7:164"]
    [receipt] = body["receipts"]
    assert receipt["kind"] == "rejected"
    assert receipt["from_state"] == "submitted"
    assert receipt["vendor_transaction_id"] == "t"
    assert receipt["detail"]["codes"][0] == {"system": "status", "code": "A7:21"}
    # The scrub still has nothing to say; the rejection is the payer's.
    assert body["findings"] == []


def test_the_clearinghouse_hop_takes_its_moment_from_the_receipt(
    tracker: dict[str, Any],
) -> None:
    submitted = _submitted(tracker)
    _acknowledge(tracker, submitted, "clearinghouse_forwarded")

    body = tracker["client"].get(f"/api/claims/{submitted['id']}").json()

    assert body["state"] == "ch_accepted"
    assert body["next_action"] == "await_payer"
    hops = {h["kind"]: h for h in body["hops"]}
    assert hops["clearinghouse_accepted"]["reached"] is True
    assert hops["clearinghouse_accepted"]["at"] is not None
    assert hops["payer_accepted"]["reached"] is False


# --- the status check ------------------------------------------------------------------


def test_a_status_check_asks_the_clearinghouse_and_returns_the_claim(
    tracker: dict[str, Any],
) -> None:
    submitted = _submitted(tracker)
    tracker["clearinghouse"].acknowledge("payer_accepted", submitted["control_number"])

    resp = tracker["client"].post(f"/api/claims/{submitted['id']}/status")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "payer_accepted"
    assert body["payer_claim_number"] == "PYR2026090600001"
    assert body["next_action"] == "await_remittance"
    assert body["patient_name"] == "John Anon"
    assert [r["kind"] for r in body["receipts"]] == ["payer_accepted"]
    [row] = [r for r in _audit_rows(tracker) if r.action == "claim_status_checked"]
    assert row.changes["state"] == "payer_accepted"
    assert "member_id" not in str(row.changes)


def test_a_status_check_with_nothing_new_records_the_look(tracker: dict[str, Any]) -> None:
    submitted = _submitted(tracker)

    resp = tracker["client"].post(f"/api/claims/{submitted['id']}/status")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["state"] == "submitted"
    assert [r["kind"] for r in body["receipts"]] == ["status_checked"]
    assert body["status_checked_at"] is not None


def test_a_status_check_without_a_clearinghouse_is_503(tracker: dict[str, Any]) -> None:
    submitted = _submitted(tracker)
    tracker["app"].dependency_overrides[get_clearinghouse_client] = lambda: None

    assert tracker["client"].post(f"/api/claims/{submitted['id']}/status").status_code == 503


def test_a_status_check_on_an_unknown_claim_is_404(tracker: dict[str, Any]) -> None:
    assert tracker["client"].post("/api/claims/nope/status").status_code == 404
