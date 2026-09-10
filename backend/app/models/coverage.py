# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Domain and API models for coverage on file: payers and a client's plan.

Two domain models — a payer the practice files with, and the coverage one
client is on — plus the request/response shapes the API speaks.

The member id and the subscriber's details are protected health information
about a named person. They are stored as typed and rendered on the chart, and
they never go into a log line or an audit ``changes`` payload; the audit rows
carry the coverage and payer row ids only.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..db.models import (
    DEFAULT_APPEAL_DAYS,
    DEFAULT_CORRECTED_CLAIM_DAYS,
    DEFAULT_TIMELY_FILING_DAYS,
)
from .claims_transport import (  # noqa: TC001 — Pydantic resolves the field types at runtime
    DocumentStatus,
    EnrollmentFieldType,
)
from .eligibility import (
    EligibilitySummary,  # noqa: TC001 — Pydantic resolves the field type at runtime
)

EnrollmentStatus = Literal["none", "filed", "pending", "active", "error"]
EnrollmentTransactionType = Literal["837P", "270", "835"]
EnrollmentRequestStatus = Literal[
    "draft",
    "stedi_action_required",
    "provider_action_required",
    "provisioning",
    "live",
    "rejected",
    "canceled",
]
SubscriberRelationship = Literal["self", "spouse", "child", "other"]
AdministrativeSex = Literal["M", "F", "U"]


# ---------------------------------------------------------------------------
# Payers
# ---------------------------------------------------------------------------


class Payer(BaseModel):
    """One insurance payer on the practice's list."""

    id: str
    name: str
    payer_id: str
    clearinghouse_payer_id: str | None = None
    is_carveout: bool = False
    carveout_of: str | None = None
    enrollment_status: EnrollmentStatus = "none"
    timely_filing_days: int = DEFAULT_TIMELY_FILING_DAYS
    corrected_claim_days: int = DEFAULT_CORRECTED_CLAIM_DAYS
    appeal_days: int = DEFAULT_APPEAL_DAYS
    created_at: datetime
    updated_at: datetime


class CreatePayerRequest(BaseModel):
    """A payer added by hand — from a client's card, or the payer directory.

    ``timely_filing_days`` is optional on purpose: left out, the service
    picks the default for the payer id (the common floor, or Medicare's
    longer window). The other two deadlines have one floor each.
    """

    name: str = Field(min_length=1, max_length=255)
    payer_id: str = Field(min_length=1, max_length=80)
    is_carveout: bool = False
    carveout_of: str | None = None
    timely_filing_days: int | None = Field(default=None, gt=0, le=3650)
    corrected_claim_days: int = Field(default=DEFAULT_CORRECTED_CLAIM_DAYS, gt=0, le=3650)
    appeal_days: int = Field(default=DEFAULT_APPEAL_DAYS, gt=0, le=3650)


class UpdatePayerRequest(BaseModel):
    """Partial update. An omitted field keeps its current value."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    payer_id: str | None = Field(default=None, min_length=1, max_length=80)
    is_carveout: bool | None = None
    carveout_of: str | None = None
    timely_filing_days: int | None = Field(default=None, gt=0, le=3650)
    corrected_claim_days: int | None = Field(default=None, gt=0, le=3650)
    appeal_days: int | None = Field(default=None, gt=0, le=3650)


class PayerResponse(BaseModel):
    """A payer as the settings form and the payer picker see it."""

    id: str
    name: str
    payer_id: str
    clearinghouse_payer_id: str | None = None
    is_carveout: bool
    carveout_of: str | None = None
    enrollment_status: EnrollmentStatus
    timely_filing_days: int
    corrected_claim_days: int
    appeal_days: int
    created_at: datetime
    updated_at: datetime


class PayerListResponse(BaseModel):
    data: list[PayerResponse]
    total: int


class PayerEnrollmentResponse(BaseModel):
    """One enrollment request with the payer, as the payer row shows it.

    ``instructions`` is the clearinghouse's own wording of what the payer
    needs when the request is waiting on the practice; ``None`` otherwise.
    """

    transaction_type: EnrollmentTransactionType
    vendor_request_id: str
    status: EnrollmentRequestStatus
    instructions: str | None = None
    updated_at: datetime


class PayerEnrollmentListResponse(BaseModel):
    """The payer's requests plus the status they add up to."""

    data: list[PayerEnrollmentResponse]
    enrollment_status: EnrollmentStatus


class EnrollmentTaskFieldResponse(BaseModel):
    """One thing a task wants answered.

    ``key`` is the clearinghouse's own identifier for the field and is what
    an answer is filed under, so it is carried through untouched rather than
    prettified. ``label`` is what the practice reads.
    """

    key: str
    label: str
    field_type: EnrollmentFieldType
    description: str | None = None


class EnrollmentTaskLinkResponse(BaseModel):
    """A form or instruction sheet the task points at."""

    label: str
    url: str


class EnrollmentTaskResponse(BaseModel):
    """One open task, shaped as the form the practice fills in.

    ``fields`` empty means the task is the instructions themselves — done in
    a payer's portal or over the telephone — and answering it is the
    practice's assertion that it did so.
    """

    id: str
    instructions: str | None = None
    links: list[EnrollmentTaskLinkResponse] = []
    fields: list[EnrollmentTaskFieldResponse] = []


class EnrollmentDocumentResponse(BaseModel):
    """A PDF on the enrollment — uploaded by the practice or by the vendor."""

    id: str
    name: str | None = None
    status: DocumentStatus


class EnrollmentTaskListResponse(BaseModel):
    """What the payer is waiting on, read live from the clearinghouse.

    ``documents`` is every PDF on the enrollment, whichever side put it
    there: the blank form the payer wants signed and the signed one sent
    back both live here.
    """

    data: list[EnrollmentTaskResponse]
    status: EnrollmentRequestStatus
    documents: list[EnrollmentDocumentResponse] = []


class EnrollmentDocumentUrlResponse(BaseModel):
    """Where to fetch one of the enrollment's PDFs from.

    Short-lived and issued by the clearinghouse's storage provider, so it is
    handed to the browser to follow rather than proxied through here.
    """

    url: str


class PayerEnrollmentRefreshResponse(BaseModel):
    """What a practice-wide refresh pass answered.

    ``throttled`` marks an answer handed back from the floor rather than a
    pass that actually asked the clearinghouse — the count and time are the
    previous pass's.
    """

    changed: int
    checked_at: datetime
    throttled: bool


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------


class SubscriberFields(BaseModel):
    """The part of a coverage that is about the subscriber, not the plan.

    Shared by the create and update requests and the response so the field
    list exists once. All optional at the schema level: they only matter when
    the subscriber is somebody other than the client, and the claim scrub —
    not this model — decides when they are required.
    """

    subscriber_relationship: SubscriberRelationship = "self"
    subscriber_first_name: str | None = Field(default=None, max_length=255)
    subscriber_last_name: str | None = Field(default=None, max_length=255)
    subscriber_date_of_birth: date | None = None
    subscriber_sex: AdministrativeSex | None = None
    subscriber_address_line1: str | None = Field(default=None, max_length=255)
    subscriber_address_line2: str | None = Field(default=None, max_length=255)
    subscriber_city: str | None = Field(default=None, max_length=100)
    subscriber_state: str | None = Field(default=None, max_length=2)
    subscriber_postal_code: str | None = Field(default=None, max_length=10)


#: Largest copay the API will take on file, in minor units. A copay is the
#: small figure printed on a card; anything this far above it was typed in
#: dollars into a cents box, and refusing is better than collecting it.
MAX_COPAY_CENTS = 100_000


class PatientCoverage(SubscriberFields):
    """The plan one client is on, as stored."""

    id: str
    patient_id: str
    payer_id: str
    member_id: str
    group_number: str | None = None
    plan_name: str | None = None
    active: bool = True
    #: What this practice collects at the door, when it knows better than
    #: the payer's answer. ``None`` is "no override", not "no copay".
    copay_override_cents: int | None = None
    last_271: dict | None = None
    verified_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class NewPayerInline(BaseModel):
    """The free-text fallback of the payer picker: a payer typed from the card.

    Creates a ``payers`` row alongside the coverage. Only the name and the
    electronic payer id are collected here; the deadlines take their defaults
    and can be edited later in Settings.
    """

    name: str = Field(min_length=1, max_length=255)
    payer_id: str = Field(min_length=1, max_length=80)


class CreateCoverageRequest(SubscriberFields):
    """Put a plan on file for a client.

    Names the payer one of two ways: ``payer_id`` for one already on the
    practice's list, or ``new_payer`` to add one from the card. Exactly one.
    """

    payer_id: str | None = None
    new_payer: NewPayerInline | None = None
    member_id: str = Field(min_length=1, max_length=80)
    group_number: str | None = Field(default=None, max_length=80)
    plan_name: str | None = Field(default=None, max_length=255)
    copay_override_cents: int | None = Field(default=None, gt=0, le=MAX_COPAY_CENTS)

    @model_validator(mode="after")
    def _exactly_one_payer(self) -> CreateCoverageRequest:
        if (self.payer_id is None) == (self.new_payer is None):
            msg = "Send either payer_id or new_payer, not both and not neither."
            raise ValueError(msg)
        return self


class UpdateCoverageRequest(BaseModel):
    """Partial update of the active coverage. An omitted field keeps its value.

    The payer may be switched to another one already on the list; adding a
    new payer on the way through is the create path's job.
    """

    payer_id: str | None = None
    member_id: str | None = Field(default=None, min_length=1, max_length=80)
    group_number: str | None = Field(default=None, max_length=80)
    plan_name: str | None = Field(default=None, max_length=255)
    #: Sent as ``null`` to take the override off again and fall back to the
    #: payer's answer — which is why an omitted field and an explicit null
    #: mean different things on this one.
    copay_override_cents: int | None = Field(default=None, gt=0, le=MAX_COPAY_CENTS)
    subscriber_relationship: SubscriberRelationship | None = None
    subscriber_first_name: str | None = Field(default=None, max_length=255)
    subscriber_last_name: str | None = Field(default=None, max_length=255)
    subscriber_date_of_birth: date | None = None
    subscriber_sex: AdministrativeSex | None = None
    subscriber_address_line1: str | None = Field(default=None, max_length=255)
    subscriber_address_line2: str | None = Field(default=None, max_length=255)
    subscriber_city: str | None = Field(default=None, max_length=100)
    subscriber_state: str | None = Field(default=None, max_length=2)
    subscriber_postal_code: str | None = Field(default=None, max_length=10)


class CoverageResponse(SubscriberFields):
    """A client's coverage as the chart card renders it, payer embedded.

    ``last_271`` is not returned here: the raw eligibility response is a
    vendor document. What the chart gets is ``eligibility``, the stored
    response read down by ``app.claims.eligibility`` — ``None`` until a
    check has run.
    """

    id: str
    patient_id: str
    payer: PayerResponse
    member_id: str
    group_number: str | None = None
    plan_name: str | None = None
    active: bool
    copay_override_cents: int | None = None
    verified_at: datetime | None = None
    eligibility: EligibilitySummary | None = None
    created_at: datetime
    updated_at: datetime


class IntakeCoverage(BaseModel):
    """Insurance as a client types it at intake, before any chart exists.

    Optional as a whole and loosely shaped on purpose: the client is reading
    a card, not a payer directory. ``payer_id`` is the electronic id if the
    card shows one; the practice fixes it up later from the payer directory.
    """

    payer_name: str = Field(min_length=1, max_length=255)
    payer_id: str | None = Field(default=None, max_length=80)
    member_id: str = Field(min_length=1, max_length=80)
    group_number: str | None = Field(default=None, max_length=80)
    plan_name: str | None = Field(default=None, max_length=255)
    subscriber_relationship: SubscriberRelationship = "self"
    subscriber_first_name: str | None = Field(default=None, max_length=255)
    subscriber_last_name: str | None = Field(default=None, max_length=255)
    subscriber_date_of_birth: date | None = None
