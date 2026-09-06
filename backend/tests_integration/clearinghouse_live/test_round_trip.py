# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The acknowledgment and remittance that follow an accepted test-payer claim.

The test payer answers every accepted claim with a 277CA and an 835 (paid in
full) a minute or two later. This module polls the transaction feed until
both have landed for this run's claim, fetches each through the report
endpoints, and pushes them through the parsers the remittance work uses.
"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app.claims.responses import parse_835, parse_polling_page
from app.claims.stedi import CORE_API_BASE, HEALTHCARE_API_BASE

from .conftest import assert_same_shape, fixture_shape

if TYPE_CHECKING:
    from app.models.claims_responses import PolledTransaction

    from .conftest import LiveClient, SubmittedClaim

_POLL_TIMEOUT = timedelta(seconds=180)
_POLL_INTERVAL_SECONDS = 10
#: The feed excludes its ``startDateTime`` and buffers the last few seconds,
#: so start a little before the submission rather than at it.
_POLL_LOOKBACK = timedelta(minutes=2)

#: Where each inbound document echoes the claim's patient control number.
_ECHO_ELEMENT = {"277": "TRN-02", "835": "CLP-01"}
_ACCEPTED_CATEGORY_CODES = {"A1", "A2"}


def _poll_once(live: LiveClient, start: str) -> tuple[list[PolledTransaction], dict[str, Any]]:
    """Every transaction processed since ``start``, following page tokens."""
    transactions: list[PolledTransaction] = []
    first_page: dict[str, Any] | None = None
    params: dict[str, Any] = {"startDateTime": start, "pageSize": 100}
    while True:
        response = live.get_raw(f"{CORE_API_BASE}/polling/transactions", params=params)
        assert response.status_code == 200
        page = response.json()
        if first_page is None:
            first_page = page
        items, next_page_token = parse_polling_page(page)
        transactions.extend(items)
        if not next_page_token or not items:
            break
        params = {"pageToken": next_page_token, "pageSize": 100}
    assert first_page is not None
    return transactions, first_page


def _inbound_for(
    transactions: list[PolledTransaction], control_number: str
) -> dict[str, PolledTransaction]:
    found: dict[str, PolledTransaction] = {}
    for transaction in transactions:
        if transaction.direction != "INBOUND":
            continue
        element = _ECHO_ELEMENT.get(transaction.transaction_set)
        if element and transaction.business_identifiers.get(element) == control_number:
            found[transaction.transaction_set] = transaction
    return found


def _wait_for_acknowledgment_and_remittance(
    live: LiveClient, claim: SubmittedClaim
) -> tuple[dict[str, PolledTransaction], dict[str, Any]]:
    start = (claim.submitted_at - _POLL_LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    deadline = time.monotonic() + _POLL_TIMEOUT.total_seconds()
    while True:
        transactions, first_page = _poll_once(live, start)
        found = _inbound_for(transactions, claim.control_number)
        if {"277", "835"} <= found.keys():
            return found, first_page
        assert time.monotonic() < deadline, (
            f"no 277CA and 835 for the claim within {_POLL_TIMEOUT.total_seconds():.0f}s; "
            f"saw {sorted(found)}"
        )
        time.sleep(_POLL_INTERVAL_SECONDS)


def _claims_in_277(report: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for transaction in report.get("transactions", []):
        for payer in transaction.get("payers", []):
            for status_transaction in payer.get("claimStatusTransactions", []):
                for detail in status_transaction.get("claimStatusDetails", []):
                    for patient in detail.get("patientClaimStatusDetails", []):
                        claims.extend(patient.get("claims", []))
    return claims


def test_the_test_payer_acknowledges_and_pays_the_claim(
    live: LiveClient, submitted_claim: SubmittedClaim
) -> None:
    found, first_page = _wait_for_acknowledgment_and_remittance(live, submitted_claim)
    assert_same_shape(first_page, fixture_shape("polling_transactions_277_and_835.json"))

    for transaction in found.values():
        document = live.adapter.get_transaction(transaction.transaction_id)
        assert document.direction == "INBOUND"
        assert transaction.mode == "test"

    acknowledgment = live.get_raw(
        f"{HEALTHCARE_API_BASE}/change/medicalnetwork/reports/v2/{found['277'].transaction_id}/277"
    )
    assert acknowledgment.status_code == 200
    claims = _claims_in_277(acknowledgment.json())
    assert [c["claimStatus"]["referencedTransactionTraceNumber"] for c in claims] == [
        submitted_claim.control_number
    ]
    category_codes = {
        status["healthCareClaimStatusCategoryCode"]
        for info in claims[0]["claimStatus"]["informationClaimStatuses"]
        for status in info["informationStatuses"]
    }
    assert category_codes <= _ACCEPTED_CATEGORY_CODES

    remittance_report = live.get_raw(
        f"{HEALTHCARE_API_BASE}/change/medicalnetwork/reports/v2/{found['835'].transaction_id}/835"
    )
    assert remittance_report.status_code == 200
    remittance_body = remittance_report.json()
    assert_same_shape(remittance_body, fixture_shape("835_report_paid_in_full.json"))

    remittances = parse_835(remittance_body)
    assert len(remittances) == 1
    paid_claims = remittances[0].claims
    assert [c.patient_control_number for c in paid_claims] == [submitted_claim.control_number]
    assert paid_claims[0].paid_cents == paid_claims[0].total_charge_cents
    assert paid_claims[0].patient_responsibility_cents == 0
    assert remittances[0].payment_amount_cents == paid_claims[0].paid_cents
    assert [line.line_control_number for line in paid_claims[0].lines] == [
        submitted_claim.control_number + "L1"
    ]
