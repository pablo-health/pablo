# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pure parsers for clearinghouse claim response JSON.

No network access: these functions take a JSON object already fetched by the
adapter and return the models in :mod:`app.models.claims_responses`. They do
not persist anything either — that is the submission and remittance work's
job. A missing field the claims work actually reads raises :class:`ParseError`
naming the JSON path; an unrecognized field is silently ignored.

Two response shapes a practice's clearinghouse account returns are
deliberately not parsed here because nothing under ``claims/`` reads them:
the outbound 837 request body it accepts, and the one-time provider record
created before a payer enrollment can be requested.
"""

from __future__ import annotations

from typing import Any

from ..models.claims_responses import (
    AcknowledgmentSource,
    Adjustment,
    ClaimAcknowledgment,
    ClaimStatus,
    ClaimSubmissionResult,
    EditRejection,
    EnrollmentRecord,
    PolledTransaction,
    Remittance,
    RemittanceClaim,
    RemittanceLine,
)
from ..money import dollars_to_cents

_MAX_ADJUSTMENT_SLOTS = 6


class ParseError(ValueError):
    """A required field was missing from a clearinghouse response."""

    def __init__(self, path: str) -> None:
        super().__init__(f"missing required field: {path}")
        self.path = path


def _require(obj: dict[str, Any], key: str, path: str) -> Any:
    value = obj.get(key)
    if value is None:
        raise ParseError(f"{path}.{key}")
    return value


def _cents(obj: dict[str, Any], key: str, path: str) -> int:
    value = dollars_to_cents(_require(obj, key, path))
    if value is None:
        raise ParseError(f"{path}.{key}")
    return value


def parse_submission(data: dict[str, Any]) -> ClaimSubmissionResult:
    """Parse the synchronous 200 or 400 body from an 837 submission."""
    claim_reference = _require(data, "claimReference", "$")
    ref_path = "$.claimReference"
    meta = _require(data, "meta", "$")

    line_control_numbers = [
        _require(line, "lineItemControlNumber", f"{ref_path}.serviceLines[{i}]")
        for i, line in enumerate(claim_reference.get("serviceLines", []))
    ]
    errors = [
        EditRejection(
            code=_require(error, "code", f"$.errors[{i}]"),
            description=_require(error, "description", f"$.errors[{i}]"),
            followup_action=_require(error, "followupAction", f"$.errors[{i}]"),
        )
        for i, error in enumerate(data.get("errors", []))
    ]

    return ClaimSubmissionResult(
        status=_require(data, "status", "$"),
        vendor_claim_id=_require(claim_reference, "correlationId", ref_path),
        patient_control_number=_require(claim_reference, "patientControlNumber", ref_path),
        line_control_numbers=line_control_numbers,
        payer_id=_require(claim_reference, "payerId", ref_path),
        errors=errors,
        trace_id=_require(meta, "traceId", "$.meta"),
    )


def _parse_polled_transaction(item: dict[str, Any], path: str) -> PolledTransaction:
    x12_metadata = _require(_require(item, "x12", path), "metadata", f"{path}.x12")
    transaction = _require(x12_metadata, "transaction", f"{path}.x12.metadata")
    business_identifiers = {
        identifier["element"]: identifier["value"]
        for identifier in item.get("businessIdentifiers", [])
    }
    return PolledTransaction(
        transaction_id=_require(item, "transactionId", path),
        direction=_require(item, "direction", path),
        mode=_require(item, "mode", path),
        processed_at=_require(item, "processedAt", path),
        transaction_set=_require(
            transaction, "transactionSetIdentifier", f"{path}.x12.metadata.transaction"
        ),
        business_identifiers=business_identifiers,
    )


def parse_polling_page(data: dict[str, Any]) -> tuple[list[PolledTransaction], str | None]:
    """Parse one page of the transaction polling feed."""
    items = [
        _parse_polled_transaction(item, f"$.items[{i}]")
        for i, item in enumerate(data.get("items", []))
    ]
    return items, data.get("nextPageToken")


def _flatten_adjustments(entries: list[dict[str, Any]], path: str) -> list[Adjustment]:
    adjustments = []
    for i, entry in enumerate(entries):
        entry_path = f"{path}[{i}]"
        group_code = _require(entry, "claimAdjustmentGroupCode", entry_path)
        for slot in range(1, _MAX_ADJUSTMENT_SLOTS + 1):
            reason_code = entry.get(f"adjustmentReasonCode{slot}")
            if reason_code is None:
                continue
            adjustments.append(
                Adjustment(
                    group_code=group_code,
                    reason_code=reason_code,
                    amount_cents=_cents(entry, f"adjustmentAmount{slot}", entry_path),
                )
            )
    return adjustments


def _parse_remittance_line(line: dict[str, Any], path: str) -> RemittanceLine:
    service_payment = _require(line, "servicePaymentInformation", path)
    payment_path = f"{path}.servicePaymentInformation"
    return RemittanceLine(
        line_control_number=_require(line, "lineItemControlNumber", path),
        service_date=_require(line, "serviceDate", path),
        cpt=_require(service_payment, "adjudicatedProcedureCode", payment_path),
        charge_cents=_cents(service_payment, "lineItemChargeAmount", payment_path),
        paid_cents=_cents(service_payment, "lineItemProviderPaymentAmount", payment_path),
        adjustments=_flatten_adjustments(
            line.get("serviceAdjustments", []), f"{path}.serviceAdjustments"
        ),
    )


def _parse_remittance_claim(payment_info: dict[str, Any], path: str) -> RemittanceClaim:
    claim_payment = _require(payment_info, "claimPaymentInfo", path)
    claim_path = f"{path}.claimPaymentInfo"
    lines = [
        _parse_remittance_line(line, f"{path}.serviceLines[{i}]")
        for i, line in enumerate(payment_info.get("serviceLines", []))
    ]
    return RemittanceClaim(
        patient_control_number=_require(claim_payment, "patientControlNumber", claim_path),
        payer_claim_control_number=_require(claim_payment, "payerClaimControlNumber", claim_path),
        claim_status_code=_require(claim_payment, "claimStatusCode", claim_path),
        total_charge_cents=_cents(claim_payment, "totalClaimChargeAmount", claim_path),
        paid_cents=_cents(claim_payment, "claimPaymentAmount", claim_path),
        patient_responsibility_cents=_cents(
            claim_payment, "patientResponsibilityAmount", claim_path
        ),
        claim_frequency_code=_require(claim_payment, "claimFrequencyCode", claim_path),
        adjustments=_flatten_adjustments(
            payment_info.get("claimAdjustments", []), f"{path}.claimAdjustments"
        ),
        lines=lines,
    )


def _parse_remittance(transaction: dict[str, Any], path: str) -> Remittance:
    payer = _require(transaction, "payer", path)
    payer_path = f"{path}.payer"
    financial = _require(transaction, "financialInformation", path)
    financial_path = f"{path}.financialInformation"

    claims = []
    for detail_i, detail in enumerate(transaction.get("detailInfo", [])):
        detail_path = f"{path}.detailInfo[{detail_i}]"
        for payment_i, payment_info in enumerate(detail.get("paymentInfo", [])):
            claims.append(
                _parse_remittance_claim(payment_info, f"{detail_path}.paymentInfo[{payment_i}]")
            )

    return Remittance(
        payer_name=_require(payer, "name", payer_path),
        payer_id=_require(payer, "centersForMedicareAndMedicaidServicesPlanId", payer_path),
        payment_method=_require(financial, "paymentMethodCode", financial_path),
        payment_date=_require(financial, "checkIssueOrEFTEffectiveDate", financial_path),
        payment_amount_cents=_cents(financial, "totalActualProviderPaymentAmount", financial_path),
        claims=claims,
    )


#: The 277CA's information-source loop names who is acknowledging: ``AY``
#: is a clearinghouse, ``PR`` a payer.
_ACKNOWLEDGMENT_SOURCES: dict[str, AcknowledgmentSource] = {"AY": "clearinghouse", "PR": "payer"}


def _parse_claim_status(
    status: dict[str, Any], path: str, effective_date: str | None, action_code: str | None
) -> ClaimStatus:
    return ClaimStatus(
        category_code=_require(status, "healthCareClaimStatusCategoryCode", path),
        category_description=status.get("healthCareClaimStatusCategoryCodeValue"),
        status_code=status.get("statusCode"),
        status_description=status.get("statusCodeValue"),
        entity_code=status.get("entityIdentifierCode"),
        effective_date=effective_date,
        action_code=action_code,
    )


def _parse_acknowledged_claim(
    claim: dict[str, Any],
    path: str,
    *,
    source: AcknowledgmentSource,
    source_name: str | None,
    batch_number: str | None,
) -> ClaimAcknowledgment:
    claim_status = _require(claim, "claimStatus", path)
    status_path = f"{path}.claimStatus"
    statuses: list[ClaimStatus] = []
    for i, info in enumerate(claim_status.get("informationClaimStatuses", [])):
        info_path = f"{status_path}.informationClaimStatuses[{i}]"
        effective_date = info.get("statusInformationEffectiveDate")
        action_code = info.get("statusInformationActionCode")
        statuses.extend(
            _parse_claim_status(
                status, f"{info_path}.informationStatuses[{j}]", effective_date, action_code
            )
            for j, status in enumerate(info.get("informationStatuses", []))
        )
    return ClaimAcknowledgment(
        source=source,
        source_name=source_name,
        control_number=_require(claim_status, "referencedTransactionTraceNumber", status_path),
        batch_number=batch_number,
        payer_claim_number=claim_status.get("tradingPartnerClaimNumber"),
        statuses=statuses,
    )


def parse_277(data: dict[str, Any]) -> list[ClaimAcknowledgment]:
    """Parse a 277CA report into one acknowledgment per claim it names.

    A report can carry several claims (payers batch them); each comes back
    with who acknowledged it — the clearinghouse or the payer — and its
    status pairs. Free-text status messages and the subscriber loop are
    not read.
    """
    acknowledgments: list[ClaimAcknowledgment] = []
    for t, transaction in enumerate(data.get("transactions", [])):
        for p, payer in enumerate(transaction.get("payers", [])):
            payer_path = f"$.transactions[{t}].payers[{p}]"
            source = _ACKNOWLEDGMENT_SOURCES.get(str(payer.get("entityIdentifierCode")), "unknown")
            source_name = payer.get("organizationName")
            for s, status_transaction in enumerate(payer.get("claimStatusTransactions", [])):
                batch_number = status_transaction.get("claimTransactionBatchNumber")
                for d, detail in enumerate(status_transaction.get("claimStatusDetails", [])):
                    for q, patient in enumerate(detail.get("patientClaimStatusDetails", [])):
                        for c, claim in enumerate(patient.get("claims", [])):
                            path = (
                                f"{payer_path}.claimStatusTransactions[{s}].claimStatusDetails[{d}]"
                                f".patientClaimStatusDetails[{q}].claims[{c}]"
                            )
                            acknowledgments.append(
                                _parse_acknowledged_claim(
                                    claim,
                                    path,
                                    source=source,
                                    source_name=source_name,
                                    batch_number=batch_number,
                                )
                            )
    return acknowledgments


def parse_835(data: dict[str, Any]) -> list[Remittance]:
    """Parse an 835 report into one remittance per transaction (check/EFT)."""
    return [
        _parse_remittance(transaction, f"$.transactions[{i}]")
        for i, transaction in enumerate(data.get("transactions", []))
    ]


def parse_enrollment(data: dict[str, Any]) -> EnrollmentRecord:
    """Parse the record created by enrolling with a payer for a transaction."""
    payer = _require(data, "payer", "$")
    transactions = data.get("transactions", {})
    transactions_requested = [
        name
        for name, request in transactions.items()
        if isinstance(request, dict) and request.get("enroll")
    ]
    return EnrollmentRecord(
        id=_require(data, "id", "$"),
        status=_require(data, "status", "$"),
        payer_stedi_id=_require(payer, "stediPayerId", "$.payer"),
        transactions_requested=transactions_requested,
    )
