# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for parsing recorded clearinghouse response fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from app.claims.responses import (
    ParseError,
    parse_277,
    parse_835,
    parse_enrollment,
    parse_polling_page,
    parse_submission,
)
from app.models.claims_responses import (
    ClaimSubmissionResult,
    EnrollmentRecord,
    PolledTransaction,
    Remittance,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

FIXTURES = Path(__file__).parent / "fixtures" / "clearinghouse"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def test_parse_submission_success() -> None:
    result = parse_submission(_load("837p_submission_success.json"))
    assert result.status == "SUCCESS"
    assert result.vendor_claim_id == "01M1T6140MJEQSJNBSF58SMB12"
    assert result.patient_control_number == "88658879"
    assert result.line_control_numbers == ["886588791"]
    assert result.payer_id == "60054"
    assert result.errors == []
    assert result.trace_id == "3edc4414-3963-4f7a-a4f8-f0794b3a4393"


def test_parse_submission_success_test_payer() -> None:
    result = parse_submission(_load("837p_submission_success_test_payer.json"))
    assert result.status == "SUCCESS"
    assert result.vendor_claim_id == "01M1T7001FRW15MVE0SSW4FA7G"
    assert result.patient_control_number == "88659891"
    assert result.line_control_numbers == ["886598911"]
    assert result.payer_id == "STEDI"
    assert result.errors == []


def test_parse_submission_edit_rejected_dx_pointer() -> None:
    result = parse_submission(_load("837p_submission_edit_rejected_dx_pointer.json"))
    assert result.status == "ERROR"
    assert len(result.errors) == 1
    assert result.errors[0].code == "33"
    assert "Diagnosis Pointer" in result.errors[0].description
    assert result.errors[0].followup_action == "Please Correct and Resubmit"


def test_parse_submission_edit_rejected_dx_specificity() -> None:
    result = parse_submission(_load("837p_submission_edit_rejected_dx_specificity.json"))
    assert result.status == "ERROR"
    assert len(result.errors) == 1
    assert result.errors[0].code == "33"
    assert "F41" in result.errors[0].description


def test_parse_submission_edit_rejected_subscriber_demographics() -> None:
    result = parse_submission(_load("837p_submission_edit_rejected_subscriber_demographics.json"))
    assert result.status == "ERROR"
    assert len(result.errors) == 2
    assert {error.code for error in result.errors} == {"33"}
    descriptions = [error.description for error in result.errors]
    assert any("date of birth" in d for d in descriptions)
    assert any("subscriber address" in d for d in descriptions)


def test_parse_submission_requires_claim_reference() -> None:
    with pytest.raises(ParseError, match=r"claimReference"):
        parse_submission({"status": "SUCCESS", "meta": {"traceId": "x"}})


def test_parse_polling_page() -> None:
    items, next_page_token = parse_polling_page(_load("polling_transactions_277_and_835.json"))
    assert next_page_token == (
        "MTc4ODY1OTk0OTk3Mzg1NHwwNTk0N2FjNi1jYzFjLTQ2MDYtYjFkNi1iZDE4YjQzZGE5ZWE="
    )
    assert [t.transaction_set for t in items] == ["837", "835", "277"]
    assert [t.direction for t in items] == ["OUTBOUND", "INBOUND", "INBOUND"]

    outbound_837 = items[0]
    assert outbound_837.transaction_id == "25593492-ef5e-4294-8268-094615ccb387"
    assert outbound_837.mode == "test"
    assert outbound_837.business_identifiers["CLM-01"] == "88659891"
    assert outbound_837.business_identifiers["BHT-03"] == "01M1T7001FRW15MVE0SSW4FA7G"

    inbound_835 = items[1]
    assert inbound_835.business_identifiers["CLP-01"] == "88659891"
    assert inbound_835.business_identifiers["TRN-02"] == "01M1T7166M0FRNNBFWHJQC7V18"


def test_parse_277_clearinghouse_forwarded() -> None:
    [ack] = parse_277(_load("277ca_report_clearinghouse_forwarded.json"))
    assert ack.source == "clearinghouse"
    assert ack.source_name == "STEDI INC"
    assert ack.control_number == "LIVE50D1D98E2364"
    assert ack.batch_number == "01M1VPM7E0T38G9WBVG4HN5C5Q"
    assert ack.payer_claim_number is None
    assert [
        (s.category_code, s.status_code, s.entity_code, s.action_code) for s in ack.statuses
    ] == [("A1", "16", "PR", "WQ")]
    assert ack.statuses[0].code == "A1:16"
    assert ack.statuses[0].effective_date == "20260906"
    assert ack.outcome == "accepted"


def test_parse_277_payer_accepted() -> None:
    [ack] = parse_277(_load("277ca_report_payer_accepted.json"))
    assert ack.source == "payer"
    assert ack.payer_claim_number == "PYR2026090600001"
    assert [s.code for s in ack.statuses] == ["A2:20"]
    assert ack.outcome == "accepted"


def test_parse_277_payer_rejected() -> None:
    [ack] = parse_277(_load("277ca_report_payer_rejected.json"))
    assert ack.source == "payer"
    assert [s.code for s in ack.statuses] == ["A7:21", "A7:164"]
    assert ack.statuses[0].status_description == "Missing or invalid information."
    assert ack.statuses[0].action_code == "U"
    assert ack.outcome == "rejected"


def test_parse_277_requires_the_echoed_control_number() -> None:
    report = _load("277ca_report_payer_accepted.json")
    status = report["transactions"][0]["payers"][0]["claimStatusTransactions"][0][
        "claimStatusDetails"
    ][0]["patientClaimStatusDetails"][0]["claims"][0]["claimStatus"]
    del status["referencedTransactionTraceNumber"]
    with pytest.raises(ParseError) as excinfo:
        parse_277(report)
    assert excinfo.value.path.endswith(".claimStatus.referencedTransactionTraceNumber")


def test_parse_277_of_an_empty_report_is_empty() -> None:
    assert parse_277({"transactions": []}) == []


def test_parse_835_paid_in_full() -> None:
    remittances = parse_835(_load("835_report_paid_in_full.json"))
    assert len(remittances) == 1
    remittance = remittances[0]

    assert remittance.payer_name == "Stedi Test Payer"
    assert remittance.payer_id == "STEDI"
    assert remittance.payment_method == "ACH"
    assert remittance.payment_date == "20260906"
    assert remittance.payment_amount_cents == 15000

    assert len(remittance.claims) == 1
    claim = remittance.claims[0]
    assert claim.patient_control_number == "88659891"
    assert claim.payer_claim_control_number == "01M1T7166MRBYFXR24M00Z5XQS"
    assert claim.claim_status_code == "1"
    assert claim.total_charge_cents == 15000
    assert claim.paid_cents == 15000
    assert claim.patient_responsibility_cents == 0
    assert claim.claim_frequency_code == "1"
    assert claim.adjustments == []

    assert len(claim.lines) == 1
    line = claim.lines[0]
    assert line.line_control_number == "886598911"
    assert line.service_date == "20260901"
    assert line.cpt == "90837"
    assert line.charge_cents == 15000
    assert line.paid_cents == 15000
    assert line.adjustments == []


def test_parse_835_converts_cents_via_money(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _load("835_report_paid_in_full.json")
    claim_payment = data["transactions"][0]["detailInfo"][0]["paymentInfo"][0]["claimPaymentInfo"]
    claim_payment["totalClaimChargeAmount"] = "150.00"
    claim_payment["patientResponsibilityAmount"] = "0.5"

    remittance = parse_835(data)[0]
    claim = remittance.claims[0]
    assert claim.total_charge_cents == 15000
    assert claim.patient_responsibility_cents == 50


def test_parse_enrollment() -> None:
    record = parse_enrollment(_load("enrollment_create_enrollment_835.json"))
    assert record.id == "01a0746f-2edf-75c0-a780-555b1231c789"
    assert record.status == "STEDI_ACTION_REQUIRED"
    assert record.payer_stedi_id == "FRCPB"
    assert record.transactions_requested == ["claimPayment"]


@pytest.mark.parametrize(
    ("loader", "fixture"),
    [
        (parse_submission, "837p_submission_success.json"),
        (parse_submission, "837p_submission_edit_rejected_dx_specificity.json"),
        (parse_enrollment, "enrollment_create_enrollment_835.json"),
    ],
)
def test_round_trip_drops_nothing_the_claims_work_reads(loader: Any, fixture: str) -> None:
    parsed: BaseModel = loader(_load(fixture))
    reparsed = type(parsed).model_validate(json.loads(parsed.model_dump_json()))
    assert reparsed == parsed


def test_round_trip_polling_page() -> None:
    items, _ = parse_polling_page(_load("polling_transactions_277_and_835.json"))
    for item in items:
        assert isinstance(item, PolledTransaction)
        reparsed = PolledTransaction.model_validate(json.loads(item.model_dump_json()))
        assert reparsed == item


def test_round_trip_remittance() -> None:
    for remittance in parse_835(_load("835_report_paid_in_full.json")):
        assert isinstance(remittance, Remittance)
        reparsed = Remittance.model_validate(json.loads(remittance.model_dump_json()))
        assert reparsed == remittance


def test_parse_submission_result_type() -> None:
    assert isinstance(
        parse_submission(_load("837p_submission_success.json")), ClaimSubmissionResult
    )


def test_parse_enrollment_result_type() -> None:
    assert isinstance(
        parse_enrollment(_load("enrollment_create_enrollment_835.json")), EnrollmentRecord
    )
