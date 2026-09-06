# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The outbox (``app.claims.submit_worker``): every validated claim leaves once.

The rows of the state table this worker owns — validated + accept ->
submitted, validated + edit rejection -> rejected, validated + a refusal
the vendor calls permanent -> stalled — and the property everything else
here exists for: a crash between the submission call and the commit is
reconciled on the next run with the key that was stored, never a fresh
one, so the vendor sees one claim.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from app.claims.clearinghouse import (
    ClearinghouseInFlightError,
    ClearinghouseNotProvisionedError,
    ClearinghouseUnavailableError,
)
from app.claims.submit_worker import submit_pending

from tests.claims_fixtures import USER_ID, billing_snapshot
from tests.claims_pipeline_fakes import (
    NOW,
    PipelineHarness,
    make_harness,
    restore_listeners,
    submission_edit_rejected,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.claims.submit_worker import SubmitSummary


class _KilledError(RuntimeError):
    """The process died after the request left and before the answer was written."""


@pytest.fixture
def harness() -> Iterator[PipelineHarness]:
    built = make_harness()
    yield built
    restore_listeners()


def _run(harness: PipelineHarness, **kwargs: object) -> SubmitSummary:
    return submit_pending(
        harness.pipeline,
        harness.client,
        harness.account,
        payers=harness.payers,
        practice_user_ids=harness.practice_users(),
        commit=harness.commit,
        **kwargs,  # type: ignore[arg-type]  # test-only passthrough
    )


# --- validated + sync accept -> submitted ------------------------------------


def test_a_validated_claim_is_filed_and_becomes_submitted(harness: PipelineHarness) -> None:
    created = harness.add(state="validated")

    summary = _run(harness)

    saved = harness.get(created.id)
    assert summary.submitted == 1
    assert saved.state == "submitted"
    assert saved.submitted_at == NOW
    assert saved.vendor_claim_id == "01M1T7001FRW15MVE0SSW4FA7G"
    assert saved.submission_pending_at is None
    [(request, key)] = harness.client.submissions
    assert saved.submission_idempotency_key == key
    assert key.startswith(f"{created.control_number}:1:")
    assert request.usageIndicator == "T"
    assert request.tradingPartnerServiceId == "STEDI"
    assert request.claimInformation.patientControlNumber == created.control_number
    assert request.claimInformation.claimSupplementalInformation is None
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert (receipt.kind, receipt.from_state, receipt.to_state) == (
        "submitted",
        "validated",
        "submitted",
    )
    assert receipt.detail["correlation_id"] == "01M1T7001FRW15MVE0SSW4FA7G"
    assert harness.listener.events == []


def test_the_pending_marker_is_committed_before_the_call(harness: PipelineHarness) -> None:
    created = harness.add(state="validated")
    seen: list[tuple[str | None, list[str]]] = []

    original = harness.client.submit_claim

    def submit(req: object, *, idempotency_key: str) -> object:
        pending = harness.get(created.id)
        seen.append((pending.submission_idempotency_key, list(harness.commits)))
        return original(req, idempotency_key=idempotency_key)  # type: ignore[arg-type]

    harness.client.submit_claim = submit  # type: ignore[method-assign]
    _run(harness)

    [(key_at_call, commits_at_call)] = seen
    assert key_at_call is not None
    assert commits_at_call == ["commit"]


# --- validated + edit rejection -> rejected -----------------------------------


def test_an_edit_rejection_moves_the_claim_to_rejected_with_the_findings(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="validated")
    harness.client.answers.append(submission_edit_rejected(created.control_number))

    summary = _run(harness)

    saved = harness.get(created.id)
    assert summary.rejected == 1
    assert saved.state == "rejected"
    assert saved.submission_pending_at is None
    assert [(f.source, f.code) for f in saved.submission_findings] == [("edit", "33")]
    assert "Diagnosis code" in saved.submission_findings[0].description
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "rejected"
    assert receipt.detail == {"codes": [{"system": "edit", "code": "33"}]}


def test_a_rejection_is_announced_with_codes_only(harness: PipelineHarness) -> None:
    created = harness.add(state="validated")
    harness.client.answers.append(submission_edit_rejected(created.control_number))

    _run(harness)

    [event] = harness.listener.events
    assert event.kind == "rejected"
    assert event.user_id == USER_ID
    assert event.control_number == created.control_number
    assert event.payer_name == "Stedi Test Payer"
    assert [(c.system, c.code, c.description) for c in event.detail.codes] == [("edit", "33", None)]
    flat = str(event.to_dict())
    for forbidden in ("123456789", "2000-01-01", "F41", "Anon", "Random St"):
        assert forbidden not in flat


# --- the crash between submit and commit --------------------------------------


def test_a_crash_after_the_call_is_replayed_with_the_stored_key(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="validated")
    harness.client.answers.append(_KilledError("SIGKILL"))

    with pytest.raises(_KilledError):
        _run(harness)

    interrupted = harness.get(created.id)
    assert interrupted.state == "validated"
    assert interrupted.submission_pending_at == NOW
    stored_key = interrupted.submission_idempotency_key
    assert stored_key is not None

    summary = _run(harness)

    assert summary.submitted == 1
    assert harness.get(created.id).state == "submitted"
    assert [key for _req, key in harness.client.submissions] == [stored_key, stored_key]


def test_a_crash_after_the_vendor_filed_it_is_reconciled_from_the_feed_without_resending(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="validated")
    harness.client.answers.append(_KilledError("SIGKILL"))
    with pytest.raises(_KilledError):
        _run(harness)
    transaction = harness.client.filed(
        created.control_number, processed_at=NOW + timedelta(seconds=5), correlation_id="01MFEED"
    )

    summary = _run(harness)

    saved = harness.get(created.id)
    assert summary.reconciled == 1
    assert saved.state == "submitted"
    assert saved.vendor_claim_id == "01MFEED"
    assert saved.submission_pending_at is None
    assert len(harness.client.submissions) == 1, "the vendor already had it"
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "submitted"
    assert receipt.detail["reconciled"] is True
    assert receipt.vendor_transaction_id == transaction


def test_reconciling_never_mints_a_second_key(harness: PipelineHarness) -> None:
    created = harness.add(state="validated")
    harness.client.answers.append(ClearinghouseUnavailableError("timeout"))
    _run(harness)
    harness.client.answers.append(ClearinghouseInFlightError("processing", retry_after=2))
    _run(harness)
    _run(harness)

    keys = {key for _req, key in harness.client.submissions}
    assert len(harness.client.submissions) == 3
    assert len(keys) == 1
    assert harness.get(created.id).state == "submitted"


# --- transient failures leave the marker; permanent refusals stall ------------


@pytest.mark.parametrize(
    "failure",
    [
        ClearinghouseUnavailableError("503"),
        ClearinghouseInFlightError("409", retry_after=None),
    ],
)
def test_a_transient_failure_leaves_the_pending_marker_for_the_next_run(
    harness: PipelineHarness, failure: Exception
) -> None:
    created = harness.add(state="validated")
    harness.client.answers.append(failure)

    summary = _run(harness)

    saved = harness.get(created.id)
    assert summary.deferred == 1
    assert saved.state == "validated"
    assert saved.submission_pending_at == NOW
    assert saved.submission_idempotency_key is not None
    assert harness.receipts.list_for_claim(created.id) == []
    assert harness.listener.events == []


def test_a_permanent_refusal_stalls_the_claim_with_the_reason(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="validated")
    harness.client.answers.append(ClearinghouseNotProvisionedError("enroll first"))

    summary = _run(harness)

    saved = harness.get(created.id)
    assert summary.stalled == 1
    assert saved.state == "stalled"
    assert saved.submission_pending_at is None
    [receipt] = harness.receipts.list_for_claim(created.id)
    assert receipt.kind == "stalled"
    assert receipt.detail["codes"] == [{"system": "status", "code": "not_provisioned"}]
    [event] = harness.listener.events
    assert event.kind == "stalled"
    assert event.detail.codes[0].code == "not_provisioned"
    assert _run(harness).stalled == 0, "a stalled claim is not retried"


def test_a_claim_the_transport_cannot_build_is_stalled_without_a_call(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="validated", billing_snapshot=billing_snapshot(npi=None))

    summary = _run(harness)

    assert summary.stalled == 1
    assert harness.get(created.id).state == "stalled"
    assert harness.client.submissions == []
    [event] = harness.listener.events
    assert event.detail.codes[0].code == "claim_incomplete"


# --- lineage and bounds ------------------------------------------------------


def test_a_corrected_claim_quotes_the_parents_payer_claim_number(
    harness: PipelineHarness,
) -> None:
    parent = harness.add(state="rejected", payer_claim_number="PYR2026090600001")
    child = harness.add(state="validated", frequency_code="7", parent_claim_id=parent.id)

    _run(harness)

    [(request, key)] = harness.client.submissions
    assert request.claimInformation.claimFrequencyCode == "7"
    assert request.claimInformation.claimSupplementalInformation is not None
    assert (
        request.claimInformation.claimSupplementalInformation.claimControlNumber
        == "PYR2026090600001"
    )
    assert key.startswith(f"{child.control_number}:7:")
    assert harness.get(parent.id).state == "rejected"


def test_keys_are_minted_per_claim(harness: PipelineHarness) -> None:
    first = harness.add(state="validated")
    second = harness.add(state="validated")

    _run(harness)

    keys = [key for _req, key in harness.client.submissions]
    assert len(set(keys)) == 2
    assert {k.split(":")[0] for k in keys} == {first.control_number, second.control_number}


def test_the_run_is_bounded_and_oldest_first(harness: PipelineHarness) -> None:
    older = harness.add(state="validated", created_at=NOW - timedelta(days=2))
    newer = harness.add(state="validated", created_at=NOW - timedelta(days=1))

    summary = _run(harness, limit=1)

    assert summary.submitted == 1
    assert harness.get(older.id).state == "submitted"
    assert harness.get(newer.id).state == "validated"


def test_claims_owned_by_another_member_of_the_practice_are_left_to_them(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="validated")
    harness.pipeline.principal_user_id = "other-clinician"

    summary = submit_pending(
        harness.pipeline,
        harness.client,
        harness.account,
        payers=harness.payers,
        practice_user_ids=[USER_ID, "other-clinician"],
        commit=harness.commit,
    )

    assert summary.submitted == 0
    assert harness.get(created.id).state == "validated"


def test_claims_whose_owner_has_left_are_filed_by_whoever_sees_them(
    harness: PipelineHarness,
) -> None:
    created = harness.add(state="validated")
    harness.pipeline.principal_user_id = "other-clinician"

    summary = submit_pending(
        harness.pipeline,
        harness.client,
        harness.account,
        payers=harness.payers,
        practice_user_ids=["other-clinician"],
        commit=harness.commit,
    )

    assert summary.submitted == 1
    assert harness.get(created.id).state == "submitted"


def test_only_validated_claims_are_picked_up(harness: PipelineHarness) -> None:
    for state in ("draft", "submitted", "rejected", "stalled", "paid"):
        harness.add(state=state)

    assert _run(harness).submitted == 0
    assert harness.client.submissions == []
