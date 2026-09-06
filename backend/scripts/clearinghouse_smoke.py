# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Local smoke test for the Stedi clearinghouse adapter against the real API.

For humans, not CI — the unit tests in ``backend/tests/`` never touch the
network. This hits the vendor's test environment with a real test key to
confirm the adapter still matches what Stedi actually returns.

Usage::

    cd backend
    CLEARINGHOUSE_API_KEY=key_test_... poetry run python scripts/clearinghouse_smoke.py

Runs three calls against the vendor's documented test payer (``STEDI``):
a payer search, an eligibility check, and a claim submission. The
submission step is expected to print the vendor's "account not
provisioned" error verbatim unless the key's account has actually
enrolled with the test payer for professional claim submission — that is
not a smoke-test failure, just the account's current enrollment state.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.claims.clearinghouse import ClearinghouseError
from app.claims.credentials import ClearinghouseCredentials
from app.claims.stedi import StediClearinghouseClient
from app.models.claims_transport import (
    ClaimSubmissionRequest,
    EligibilityProvider,
    EligibilityRequest,
    EligibilitySubscriber,
)

_FIXTURE_SUBMISSION_REQUEST = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "clearinghouse"
    / "837p_request_test_payer.json"
)

_TEST_PAYER_ID = "STEDI"


def main() -> int:
    api_key = os.environ.get("CLEARINGHOUSE_API_KEY")
    if not api_key:
        print("Set CLEARINGHOUSE_API_KEY to a test key from the Stedi dashboard.")
        return 1

    if not api_key.startswith("key_test_"):
        print("Refusing to run: this script only runs against a test key.")
        return 1

    client = StediClearinghouseClient(ClearinghouseCredentials(api_key=api_key, mode="test"))

    print("--- search_payers('Stedi Test Payer') ---")
    payers = client.search_payers("Stedi Test Payer")
    if payers:
        print(f"hit: {payers[0].displayName} ({payers[0].primaryPayerId})")
    else:
        print("no hits")

    print("--- check_eligibility(payer=STEDI) ---")
    eligibility_req = EligibilityRequest(
        tradingPartnerServiceId=_TEST_PAYER_ID,
        provider=EligibilityProvider(organizationName="Pablo Test Practice", npi="1999999984"),
        subscriber=EligibilitySubscriber(memberId="123456789"),
    )
    eligibility = client.check_eligibility(eligibility_req)
    print(f"trace id: {eligibility.meta.traceId}")
    print(f"plan status: {[s.statusCode for s in eligibility.planStatus]}")

    print("--- submit_claim(payer=STEDI) ---")
    submission_body = json.loads(_FIXTURE_SUBMISSION_REQUEST.read_text())
    submission_req = ClaimSubmissionRequest.model_validate(submission_body)
    try:
        result = client.submit_claim(submission_req, idempotency_key=secrets.token_urlsafe(24))
        print(f"status: {result.status}")
        if result.claimReference:
            print(f"claim reference: {result.claimReference.rhclaimNumber}")
        for error in result.errors:
            print(f"edit rejection: {error.code} {error.description}")
    except ClearinghouseError as exc:
        print(f"vendor error: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
