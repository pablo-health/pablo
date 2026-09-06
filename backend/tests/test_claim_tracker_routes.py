# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The tracker routes (``app.routes.claim_tracker``) and the claim detail.

Built on the same in-memory harness as the claim routes, with the tracker
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
from app.routes import claim_tracker
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
    app.include_router(claim_tracker.router)
    client = FakeClearinghouse()
    receipts = app.dependency_overrides[get_claim_receipt_repository]()
    app.dependency_overrides[get_clearinghouse_client] = lambda: client
    app.dependency_overrides[get_db_session] = object
    yield {**harness, "clearinghouse": client, "receipts": receipts}
    restore_listeners()


def _submitted(tracker: dict[str, Any]) -> dict[str, Any]:
    """A validated claim moved to submitted an hour ago, as the outbox would leave it."""
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


def test_the_tracker_lists_claims_newest_first_with_what_the_ui_needs(
    tracker: dict[str, Any],
) -> None:
    first = _build(tracker)
    second = _validated(tracker)

    resp = tracker["client"].get("/api/claims")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert [c["id"] for c in body["data"]] == [second["id"], first["id"]]
    newest, oldest = body["data"]
    assert newest["next_action"] == "queued_to_send"
    assert oldest["next_action"] == "review_and_file"
    assert newest["deadline"]["applicable"] == "filing"
    assert newest["deadline"]["filing"] is not None
    assert newest["receipts"] == []
    assert newest["submission_findings"] == []
    [row] = [r for r in _audit_rows(tracker) if r.action == "claims_tracker_viewed"]
    assert row.changes["claim_ids"] == [second["id"], first["id"]]


def test_the_tracker_filters_by_state(tracker: dict[str, Any]) -> None:
    _build(tracker)
    validated = _validated(tracker)

    resp = tracker["client"].get("/api/claims", params={"state": ["validated", "submitted"]})

    assert resp.status_code == 200
    assert [c["id"] for c in resp.json()["data"]] == [validated["id"]]
    assert tracker["client"].get("/api/claims", params={"state": "bogus"}).status_code == 422


def test_the_claim_detail_carries_its_receipts(tracker: dict[str, Any]) -> None:
    submitted = _submitted(tracker)
    pipeline = ClaimPipeline(
        claims=tracker["claims"],
        receipts=tracker["receipts"],
        session=object(),  # type: ignore[arg-type]
        principal_user_id=_USER_ID,
        now=lambda: NOW,
    )
    [ack] = parse_277(
        acknowledgment_report("payer_rejected", submitted["control_number"], transaction_id="t")
    )
    apply_acknowledgment(pipeline, ack, Provenance(transaction_id="t", occurred_at=NOW))

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
