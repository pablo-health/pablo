# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain and API models for a claim built from a session.

A claim is a snapshot: everything the payer will see is copied onto it when
it is built, so the row reads the same way forever regardless of what
happens to the appointment, the coverage or the billing profile afterwards.
The two snapshot models here — the billing side and the subscriber side —
are the typed shape of the ``billing_snapshot`` / ``subscriber_snapshot``
JSON columns.

The subscriber snapshot holds a named person's date of birth and address and
the claim holds diagnosis codes. Both are protected health information: they
are stored on the row, returned to the chart, and never written to a log
line or an audit ``changes`` payload. Audit rows carry the claim id, the
control number, the state and the payer row id only.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

# Runtime import: Pydantic resolves these annotations at runtime for
# validation, so they cannot live in a TYPE_CHECKING block.
from .coverage import AdministrativeSex, SubscriberRelationship  # noqa: TC001

ClaimState = Literal[
    "draft",
    "validated",
    "submitted",
    "ch_accepted",
    "payer_accepted",
    "paid",
    "partial",
    "denied",
    "rejected",
    "stalled",
]
FrequencyCode = Literal["1", "7", "8"]
FindingSeverity = Literal["blocking", "warning"]


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


class BillingProviderSnapshot(BaseModel):
    """The practice as it files the claim: the 837P billing-provider loop.

    ``npi`` is the NPI the claim is billed under — the practice's own when
    it has one, otherwise the rendering clinician's, since a solo practice
    bills under its clinician. The tax id itself is never copied here; the
    submission decrypts it at the moment of filing.
    """

    legal_name: str | None = None
    tax_id_last4: str | None = None
    tax_id_type: Literal["ein", "ssn"] | None = None
    npi: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None


class RenderingProviderSnapshot(BaseModel):
    """The clinician who delivered the service: the rendering-provider loop."""

    user_id: str
    first_name: str | None = None
    last_name: str | None = None
    npi: str | None = None
    taxonomy_code: str | None = None


class BillingSnapshot(BaseModel):
    billing_provider: BillingProviderSnapshot
    rendering_provider: RenderingProviderSnapshot


class PersonSnapshot(BaseModel):
    """A named person's demographics as the claim carries them."""

    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: date | None = None
    sex: AdministrativeSex | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    phone: str | None = None


class SubscriberSnapshot(BaseModel):
    """The plan and the people on it as they stood when the claim was built.

    When the client is the subscriber, ``subscriber`` is a copy of
    ``patient``; otherwise it is the subscriber the coverage names.
    ``payer_id`` is the payer's electronic id (the one on the card), not the
    ``payers`` row id — the row id lives on the claim itself.
    """

    member_id: str
    group_number: str | None = None
    plan_name: str | None = None
    relationship: SubscriberRelationship = "self"
    coverage_active: bool = True
    payer_id: str
    payer_name: str
    subscriber: PersonSnapshot
    patient: PersonSnapshot


# ---------------------------------------------------------------------------
# The claim
# ---------------------------------------------------------------------------


class ClaimLine(BaseModel):
    """One service line: a code on a date for an amount."""

    id: str
    claim_id: str
    patient_id: str
    appointment_id: str | None = None
    line_number: int
    line_control_number: str
    service_date: date
    cpt: str
    modifiers: list[str] = Field(default_factory=list)
    units: int = 1
    charge_cents: int
    dx_pointers: list[int] = Field(default_factory=list)
    telehealth: bool = False
    allowed_cents: int | None = None
    paid_cents: int = 0
    patient_resp_cents: int | None = None
    adjustments: list[dict] | None = None
    created_at: datetime


class Claim(BaseModel):
    """A claim as stored, lines included."""

    id: str
    control_number: str
    patient_id: str
    coverage_id: str
    payer_id: str
    state: ClaimState = "draft"
    frequency_code: FrequencyCode = "1"
    parent_claim_id: str | None = None
    total_charge_cents: int
    total_paid_cents: int = 0
    diagnosis_codes: list[str] = Field(default_factory=list)
    place_of_service: str | None = None
    billing_snapshot: BillingSnapshot
    subscriber_snapshot: SubscriberSnapshot
    submitted_at: datetime | None = None
    payer_accepted_at: datetime | None = None
    adjudicated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    lines: list[ClaimLine] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# API shapes
# ---------------------------------------------------------------------------


class AddOnService(BaseModel):
    """A psychotherapy add-on delivered alongside the visit's own code.

    The appointment carries one service code. When the visit was an
    evaluation-and-management service with psychotherapy on top (90833,
    90836, 90838) or a crisis session that ran long (90840), the caller
    names the add-on here and the claim gets a second line for it on the
    same date. The charge is the caller's because the rate table holds one
    rate per visit, not per code.
    """

    cpt: str = Field(min_length=5, max_length=5, pattern=r"^\d{5}$")
    charge_cents: int = Field(ge=0)


class BuildClaimRequest(BaseModel):
    """Optional body for building a claim from a session."""

    add_on: AddOnService | None = None


class FindingResponse(BaseModel):
    """One thing the scrub found wrong, or worth a look, on a claim."""

    severity: FindingSeverity
    code: str
    message: str
    field: str | None = None


class ClaimResponse(Claim):
    """A claim as the chart and the claims tracker see it."""


class ClaimListResponse(BaseModel):
    data: list[ClaimResponse]
    total: int


class ValidateClaimResponse(BaseModel):
    """A claim that passed the scrub, with any warnings that came with it."""

    claim: ClaimResponse
    findings: list[FindingResponse]


class ClaimValidationFailed(BaseModel):
    """The ``detail`` of a 422 from ``/validate``: the claim stays a draft."""

    message: str
    findings: list[FindingResponse]
