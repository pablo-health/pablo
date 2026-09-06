# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Parsed shapes for clearinghouse claim responses.

These models cover exactly the fields the claims workflow reads out of a
clearinghouse's JSON: the synchronous accept/reject for an 837 submission,
the polling feed that reports what happened to a transaction afterward, the
835 remittance a payer sends back, and the record created when a practice
enrolls with a payer for electronic remittance. They are not an X12 model —
free-text segments, envelope metadata, and anything the claims work never
reads are left out on purpose.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class EditRejection(BaseModel):
    """One clearinghouse edit that rejected a submitted claim."""

    code: str
    description: str
    followup_action: str


class ClaimSubmissionResult(BaseModel):
    """The synchronous response to submitting an 837 claim.

    Both the 200 (accepted) and 400 (edit-rejected) bodies parse into this
    one model — ``errors`` is simply empty on acceptance.
    """

    status: Literal["SUCCESS", "ERROR"]
    vendor_claim_id: str
    patient_control_number: str
    line_control_numbers: list[str]
    payer_id: str
    errors: list[EditRejection]
    trace_id: str


class PolledTransaction(BaseModel):
    """One entry from the clearinghouse's transaction polling feed."""

    transaction_id: str
    direction: Literal["INBOUND", "OUTBOUND"]
    mode: str
    processed_at: str
    transaction_set: Literal["837", "277", "835"]
    business_identifiers: dict[str, str]


class Adjustment(BaseModel):
    """One claim adjustment reason/amount pair from an 835."""

    group_code: str
    reason_code: str
    amount_cents: int


class RemittanceLine(BaseModel):
    """One service line's adjudication from an 835."""

    line_control_number: str
    service_date: str
    cpt: str
    charge_cents: int
    paid_cents: int
    adjustments: list[Adjustment]


class RemittanceClaim(BaseModel):
    """One claim's adjudication from an 835."""

    patient_control_number: str
    payer_claim_control_number: str
    claim_status_code: str
    total_charge_cents: int
    paid_cents: int
    patient_responsibility_cents: int
    claim_frequency_code: str
    adjustments: list[Adjustment]
    lines: list[RemittanceLine]


class Remittance(BaseModel):
    """One payment (check or EFT) reported by an 835, with its claims."""

    payer_name: str
    payer_id: str
    payment_method: str
    payment_date: str
    payment_amount_cents: int
    claims: list[RemittanceClaim]


class EnrollmentRecord(BaseModel):
    """The result of enrolling a practice with a payer for a transaction."""

    id: str
    status: str
    payer_stedi_id: str
    transactions_requested: list[str]
