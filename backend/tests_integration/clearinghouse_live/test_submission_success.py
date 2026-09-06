# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An accepted test-payer claim, and the vendor's idempotency contract around it.

The vendor keys replay detection on an ``Idempotency-Key`` header (24 h):
the same key with the same body answers with the original result, the same
key with a different body is refused with 422. The adapter sends no such
header today, so the lane attaches one at the HTTP client level.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.claims.clearinghouse import ClearinghouseUnavailableError
from app.models.claims_transport import ClaimSubmissionRequest

from .conftest import TEST_PAYER_ID, assert_same_shape, fixture_shape

if TYPE_CHECKING:
    from .conftest import LiveClient, SubmittedClaim

_RECORDING = "837p_submission_success_test_payer.json"
_HTTP_UNPROCESSABLE = 422
_REQUEST_CHANGED = "REQUEST_CHANGED"


def test_the_test_payer_accepts_the_claim(submitted_claim: SubmittedClaim) -> None:
    result = submitted_claim.result

    assert result.status == "SUCCESS"
    assert result.claimReference is not None
    assert result.claimReference.patientControlNumber == submitted_claim.control_number
    assert result.claimReference.payerId == TEST_PAYER_ID
    assert [line.lineItemControlNumber for line in result.claimReference.serviceLines] == [
        submitted_claim.control_number + "L1"
    ]
    assert_same_shape(submitted_claim.raw, fixture_shape(_RECORDING))


def test_replaying_the_same_key_and_body_returns_the_same_claim(
    live: LiveClient, submitted_claim: SubmittedClaim
) -> None:
    replay = live.with_idempotency_key(submitted_claim.idempotency_key).adapter.submit_claim(
        ClaimSubmissionRequest.model_validate(submitted_claim.body)
    )

    assert replay.status == "SUCCESS"
    assert replay.claimReference is not None
    assert submitted_claim.result.claimReference is not None
    assert (
        replay.claimReference.correlationId == submitted_claim.result.claimReference.correlationId
    )
    assert_same_shape(live.recorder.last_json(), fixture_shape(_RECORDING))


def test_reusing_the_key_with_a_changed_body_is_refused(
    live: LiveClient, submitted_claim: SubmittedClaim
) -> None:
    changed = ClaimSubmissionRequest.model_validate(submitted_claim.body)
    changed.claimInformation.claimChargeAmount = "160.00"
    changed.claimInformation.serviceLines[0].professionalService.lineItemChargeAmount = "160.00"

    # The adapter has no typed error for 422 yet; it surfaces as "unavailable".
    with pytest.raises(ClearinghouseUnavailableError):
        live.with_idempotency_key(submitted_claim.idempotency_key).adapter.submit_claim(changed)

    assert live.recorder.last_status() == _HTTP_UNPROCESSABLE
    assert live.recorder.last_json().get("code") == _REQUEST_CHANGED
