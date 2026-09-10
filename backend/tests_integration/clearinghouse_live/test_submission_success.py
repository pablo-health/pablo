# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An accepted test-payer claim, and the vendor's idempotency contract around it.

Submission goes to the vendor's own claim API, so there is no HTTP response
to inspect here — the contract is what the adapter returns. What matters is
that an accepted claim comes back with the id its whole later life hangs
off, and that resending is safe: the same key with the same claim replays
the original answer, and the same key with a different claim is refused.

Both are exercised through the adapter's keyed parameter, so what passes
here is the contract a caller actually gets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.claims.clearinghouse import ClearinghouseRequestChangedError
from app.models.claims_transport import ClaimSubmissionRequest

from .conftest import TEST_PAYER_ID

if TYPE_CHECKING:
    from .conftest import LiveClient, SubmittedClaim

#: The vendor prefixes a claim id; a submission id (``sbm_``) in this slot
#: would be a claim that cannot find its own payments after a correction.
_CLAIM_ID_PREFIX = "clm_"


def test_the_test_payer_accepts_the_claim(submitted_claim: SubmittedClaim) -> None:
    result = submitted_claim.result

    assert result.status == "SUCCESS", [error.description for error in result.errors]
    assert result.claimReference is not None
    assert result.claimReference.patientControlNumber == submitted_claim.control_number
    assert result.claimReference.payerId == TEST_PAYER_ID
    assert result.claimReference.correlationId.startswith(_CLAIM_ID_PREFIX)


def test_replaying_the_same_key_and_body_returns_the_same_claim(
    live: LiveClient, submitted_claim: SubmittedClaim
) -> None:
    """The property every retry depends on.

    A submission that times out is retried with the same key; if that filed
    a second claim, the payer would be billed twice for one session.
    """
    replay = live.adapter.submit_claim(
        ClaimSubmissionRequest.model_validate(submitted_claim.body),
        idempotency_key=submitted_claim.idempotency_key,
    )

    assert replay.status == "SUCCESS"
    assert replay.claimReference is not None
    assert submitted_claim.result.claimReference is not None
    assert (
        replay.claimReference.correlationId == submitted_claim.result.claimReference.correlationId
    )


def test_reusing_the_key_with_a_changed_body_is_refused(
    live: LiveClient, submitted_claim: SubmittedClaim
) -> None:
    """And is refused as its own thing, not as a malformed claim.

    The vendor reports both with the same exception type, so this is the
    test that would notice the adapter conflating them and sending a biller
    to hunt for a fault in a claim that is perfectly correct.
    """
    changed = ClaimSubmissionRequest.model_validate(submitted_claim.body)
    changed.claimInformation.claimChargeAmount = "160.00"
    changed.claimInformation.serviceLines[0].professionalService.lineItemChargeAmount = "160.00"

    with pytest.raises(ClearinghouseRequestChangedError):
        live.adapter.submit_claim(changed, idempotency_key=submitted_claim.idempotency_key)
