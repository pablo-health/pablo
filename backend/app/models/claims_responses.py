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


AcknowledgmentSource = Literal["clearinghouse", "payer", "unknown"]
"""Who is speaking in a 277CA: the clearinghouse (``AY`` in the payer loop)
saying it has the claim and has passed it on, or the payer (``PR``)."""

AcknowledgmentOutcome = Literal["accepted", "rejected", "unknown"]

#: Claim status category codes that mean the claim is moving forward:
#: forwarded, received, accepted for adjudication, split.
_ACCEPTED_CATEGORIES = frozenset({"A0", "A1", "A2", "A5"})
#: ...and the ones that mean it was refused: returned as unprocessable, not
#: found, or rejected for missing / invalid / relational information.
_REJECTED_CATEGORIES = frozenset({"A3", "A4", "A6", "A7", "A8"})
_REJECT_ACTION_CODE = "U"


class ClaimStatus(BaseModel):
    """One status pair off a 277CA: what happened, and to whom it refers."""

    category_code: str
    category_description: str | None = None
    status_code: str | None = None
    status_description: str | None = None
    entity_code: str | None = None
    effective_date: str | None = None
    action_code: str | None = None

    @property
    def code(self) -> str:
        """The pair as one token, ``A7:21``, for a finding or a reminder."""
        if not self.status_code:
            return self.category_code
        return f"{self.category_code}:{self.status_code}"


class ClaimAcknowledgment(BaseModel):
    """What a 277CA says about one claim.

    ``control_number`` is the patient control number the claim was filed
    under, echoed back; ``batch_number`` is the clearinghouse's id for the
    filing (its ``correlationId``); ``payer_claim_number`` the payer's own
    number, present once the payer has one.
    """

    source: AcknowledgmentSource
    source_name: str | None = None
    control_number: str
    batch_number: str | None = None
    payer_claim_number: str | None = None
    statuses: list[ClaimStatus]

    @property
    def outcome(self) -> AcknowledgmentOutcome:
        """Accepted, rejected, or nothing this code knows how to read.

        One rejecting status rejects the claim — a payer that accepts the
        claim and rejects a line reports both, and the claim is not
        moving. Otherwise any accepting status accepts it.
        """
        categories = {status.category_code for status in self.statuses}
        actions = {status.action_code for status in self.statuses}
        if categories & _REJECTED_CATEGORIES or _REJECT_ACTION_CODE in actions:
            return "rejected"
        if categories & _ACCEPTED_CATEGORIES:
            return "accepted"
        return "unknown"


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
