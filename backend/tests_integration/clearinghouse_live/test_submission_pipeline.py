# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The outbox and the status poll against the vendor's test payer.

Where the other modules in this lane call the adapter directly, this one
drives the pipeline that a practice actually runs: a validated claim in
the repository, the outbox worker filing it through the adapter, and the
status worker reading the feed until the acknowledgement lands. Plus the
other synchronous row: a claim with a deliberate defect, refused by the
vendor's edits and moved to ``rejected`` by the same worker.

What the test payer can and cannot show. It answers every accepted claim
with a 277CA from the clearinghouse itself (``STEDI INC``, loop ``AY``)
saying the claim was forwarded to the payer, and then an 835 — it never
sends a payer-sourced 277CA. So the hop this lane observes end to end is
``validated -> submitted -> ch_accepted``; the ``payer_accepted`` hop is
the same code path driven by a payer-sourced (``PR``) acknowledgement,
proven in the unit suite from the constructed fixture. The 835 belongs to
the remittance work.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from app.claims.status_worker import poll_acknowledgments
from app.claims.submit_worker import submit_pending

from .conftest import TEST_PAYER_ID, fresh_control_number

if TYPE_CHECKING:
    from app.models.claims import Claim

    from .conftest import LiveClient

_ACK_TIMEOUT = timedelta(seconds=180)
_POLL_INTERVAL_SECONDS = 10
_EDIT_REJECTED = "33"


def _harness() -> Any:
    from tests.claims_pipeline_fakes import make_harness, restore_listeners  # noqa: PLC0415

    built = make_harness(now=datetime.now(UTC))
    built.pipeline.now = lambda: datetime.now(UTC)
    return built, restore_listeners


def _validated(harness: Any, **overrides: Any) -> Claim:
    return harness.add(state="validated", control_number=fresh_control_number(), **overrides)


def _submit(harness: Any, live: LiveClient) -> Any:
    return submit_pending(
        harness.pipeline,
        live.adapter,
        harness.account,
        payers=harness.payers,
        practice_user_ids=harness.practice_users(),
        commit=harness.commit,
    )


def _wait_for_acknowledgment(harness: Any, live: LiveClient, claim_id: str) -> Claim:
    deadline = time.monotonic() + _ACK_TIMEOUT.total_seconds()
    for attempt in range(1, 1000):
        # The poll backstop only asks about a claim once an hour; the lane
        # cannot wait that long, so its clock jumps a further hour ahead on
        # every attempt (the poll stamps each look with that clock).
        hours_ahead = timedelta(hours=attempt)
        harness.pipeline.now = lambda: datetime.now(UTC) + hours_ahead  # noqa: B023
        poll_acknowledgments(
            harness.pipeline, live.adapter, practice_user_ids=harness.practice_users()
        )
        current = harness.get(claim_id)
        if current.state != "submitted":
            return current
        assert time.monotonic() < deadline, (
            f"no acknowledgment for the claim within {_ACK_TIMEOUT.total_seconds():.0f}s"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise AssertionError("unreachable: the deadline check above trips first")


def test_the_outbox_files_the_claim_and_the_poll_hears_it_acknowledged(
    live: LiveClient,
) -> None:
    harness, restore = _harness()
    try:
        created = _validated(harness)

        summary = _submit(harness, live)

        submitted = harness.get(created.id)
        assert summary.submitted == 1
        assert submitted.state == "submitted"
        assert submitted.vendor_claim_id
        assert submitted.submission_pending_at is None
        assert live.recorder.last_json()["claimReference"]["payerId"] == TEST_PAYER_ID
        assert live.recorder.last_json()["claimReference"]["correlationId"] == (
            submitted.vendor_claim_id
        )

        acknowledged = _wait_for_acknowledgment(harness, live, created.id)

        assert acknowledged.state == "ch_accepted"
        assert acknowledged.last_receipt_at is not None
        kinds = [r.kind for r in harness.receipts.list_for_claim(created.id)]
        assert kinds == ["submitted", "ch_accepted"]
        receipt = harness.receipts.list_for_claim(created.id)[-1]
        assert receipt.detail["source"] == "clearinghouse"
        assert receipt.detail["batch_number"] == submitted.vendor_claim_id
        assert receipt.vendor_transaction_id
        assert harness.listener.events == []
    finally:
        restore()


def test_a_claim_the_vendors_edits_refuse_is_rejected_by_the_outbox(live: LiveClient) -> None:
    harness, restore = _harness()
    try:
        # A diagnosis category with no specificity fails the vendor's front-end edits.
        created = _validated(harness, diagnosis_codes=["F41"])

        summary = _submit(harness, live)

        rejected = harness.get(created.id)
        assert summary.rejected == 1
        assert rejected.state == "rejected"
        assert live.recorder.last_status() == 400
        assert {f.code for f in rejected.submission_findings} == {_EDIT_REJECTED}
        assert all(f.source == "edit" for f in rejected.submission_findings)
        [event] = harness.listener.events
        assert event.kind == "rejected"
        assert [(c.system, c.code) for c in event.detail.codes] == [("edit", _EDIT_REJECTED)]
    finally:
        restore()
