# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient export document: what ``patient.json`` in an export archive holds.

The model tree is the contract. ``schema.json`` in the same archive is this
tree's JSON Schema, written verbatim, so a consumer validates against the
exact shape it was given rather than against documentation.

Object names follow FHIR where that costs nothing (a session is an
``Encounter``, a note a ``DocumentReference``, the clinician a
``Practitioner``, a scored instrument an ``Observation``, a medication a
``MedicationStatement``, a diagnosis a ``Condition``) so a FHIR bundle can
be derived later, while JSON keys stay snake_case. Every timestamp is ISO-8601 with an offset.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION: Final = "1.4"


def _with_offset(value: datetime) -> datetime:
    """A stored timestamp without a zone is UTC; give it the offset that says so."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


Timestamp = Annotated[datetime, AfterValidator(_with_offset)]


class ExportOptions(BaseModel):
    """What the caller chose to include beyond the default record copy."""

    model_config = ConfigDict(frozen=True)

    include_transcripts: bool = False
    include_psychotherapy_notes: bool = False


class ExportPatient(BaseModel):
    """The client's demographics."""

    identifier: str = Field(description="Pablo's id for the client.")
    first_name: str
    last_name: str
    birth_date: str | None = Field(default=None, description="YYYY-MM-DD, when recorded.")
    sex: str | None = Field(default=None, description="Administrative sex: M, F or U.")
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    status: str
    diagnosis: str | None = None
    chart_closed_at: Timestamp | None = None
    created_at: Timestamp
    updated_at: Timestamp


class Practitioner(BaseModel):
    """Who the record comes from, as the practice has set itself up to bill."""

    name: str | None = None
    npi: str | None = Field(default=None, description="National Provider Identifier.")
    taxonomy_code: str | None = Field(default=None, description="NUCC provider taxonomy code.")


class ExportTranscript(BaseModel):
    format: str
    content: str


class DocumentReference(BaseModel):
    """One note. ``final_content`` is what the clinician signed off on."""

    id: str
    note_type: str
    restricted: bool = Field(description="A psychotherapy note, readable by its author alone.")
    content: dict[str, Any] | None
    content_edited: dict[str, Any] | None
    final_content: dict[str, Any] | None
    was_edited: bool
    created_at: Timestamp
    finalized_at: Timestamp | None


class Encounter(BaseModel):
    """One session, with its note when it has one.

    ``transcript`` is absent, not null, when transcripts were not included,
    so an omitted transcript never reads as an empty one; the document's
    ``options`` say which it was.
    """

    id: str
    session_number: int
    session_date: Timestamp
    status: str
    created_at: Timestamp
    transcript: ExportTranscript | None = Field(default=None, exclude_if=lambda v: v is None)
    document_reference: DocumentReference | None


ExportDocumentCategory = Literal[
    "chart", "consent", "intake_artifact", "message", "psychotherapy_notes"
]


class ExportDocument(BaseModel):
    """One uploaded file, carried in the archive as its original bytes.

    ``bytes`` and ``sha256`` describe the file at ``archive_path`` exactly,
    so a consumer can check its copy against this entry alone.
    """

    id: str
    category: ExportDocumentCategory = Field(description="What the file is filed as on the chart.")
    filename: str = Field(description="The name the file was uploaded with.")
    content_type: str
    bytes: int
    sha256: str
    uploaded_at: Timestamp
    uploaded_by: Literal["clinician", "patient"] = Field(
        description="Who put the file on the chart, by role."
    )
    archive_path: str = Field(description="Where the file is in this archive.")


class ExportAppointment(BaseModel):
    """One appointment on the schedule, whatever became of it."""

    id: str
    start: Timestamp
    end: Timestamp
    timezone: str = Field(description="IANA time zone of the calendar the appointment is on.")
    appointment_type: str = Field(description="The type's name as it was when booked.")
    status: str = Field(description="pending, confirmed, cancelled, no_show or completed.")
    clinician_name: str | None = Field(description="Whose calendar the appointment is on.")
    telehealth: bool = Field(description="Held by video rather than in person.")
    place_of_service: str | None = Field(description="CMS place-of-service code, when recorded.")
    note_type: str = Field(description="The note type a session started from it is written in.")
    service_code: str | None = Field(default=None, description="The CPT code the visit bills as.")
    session_id: str | None = Field(description="The session held for it, in sessions[].")


OutcomeMeasureSource = Literal[
    "patient_self_report", "clinician_administered_verbal", "manual", "inferred"
]


class Observation(BaseModel):
    """One administration of a scored instrument, such as a PHQ-9."""

    id: str
    instrument: str = Field(description="The instrument's short code, such as phq9.")
    instrument_name: str | None = Field(description="The instrument's name, when Pablo knows it.")
    administered_at: Timestamp
    total_score: int | None
    severity: str | None = Field(description="The band the total score falls in.")
    item_responses: dict[str, int] | None = Field(
        description="The score given to each item, keyed by item number."
    )
    is_complete: bool = Field(description="Every item was answered.")
    source: OutcomeMeasureSource = Field(description="Who answered it, and how.")
    session_id: str | None


MessageSender = Literal["patient", "clinician", "practice"]


class Communication(BaseModel):
    """One secure message."""

    id: str
    sender: MessageSender
    sent_at: Timestamp
    body: str
    attachment_document_ids: list[str] = Field(
        description="Files sent with the message, by their id in documents[]."
    )


class ExportMessageThread(BaseModel):
    """One secure-message conversation between the client and the practice."""

    id: str
    subject: str | None
    status: Literal["open", "closed"]
    created_at: Timestamp
    closed_at: Timestamp | None
    messages: list[Communication] = Field(description="Oldest first.")


class MedicationStatement(BaseModel):
    """One medication on the client's medication list."""

    id: str
    drug_name: str
    dose: str
    status: Literal["active", "discontinued", "on_hold"]
    started_on: date | None
    stopped_on: date | None
    stop_reason: str | None
    notes: str | None


class Condition(BaseModel):
    """One diagnostic assessment and the diagnosis it recorded."""

    id: str
    icd10_code: str | None = Field(description="The ICD-10-CM code the clinician confirmed.")
    description: str | None
    assessed_at: Timestamp
    status: Literal["confirmed", "unconfirmed"] = Field(
        description="confirmed once the clinician has chosen a code."
    )
    instrument: str = Field(description="The diagnostic definition the assessment followed.")
    meets_criteria: bool | None = Field(
        description="Whether the recorded responses meet the definition's criteria; "
        "null for a checklist, which makes no determination."
    )
    session_id: str | None


PaymentMethod = Literal["card", "cash", "check", "other"]


class ExportCharge(BaseModel):
    """One row of the client's ledger: a charge, a payment, an adjustment.

    How a card payment was taken is a category, never the card: no brand,
    no last four digits and no processor identifier are in any export.
    """

    id: str
    kind: str = Field(
        description="session, copay, payment, patient_resp, contractual_adjustment, "
        "write_off or credit."
    )
    appointment_id: str | None = Field(description="The visit the row is for, in appointments[].")
    claim_id: str | None = Field(description="The claim the row was posted from, in claims[].")
    description: str | None = Field(description="What the practice wrote about the row.")
    amount_cents: int = Field(description="Always positive; kind says which way it goes.")
    currency: str
    status: str = Field(
        description="pending, succeeded, failed, refunded, disputed or dispute_lost."
    )
    method: PaymentMethod | None = Field(description="How a payment arrived, on rows that collect.")
    payment_reference: str | None = Field(description="A check number or similar.")
    write_off_reason: str | None
    settled_by_charge_id: str | None = Field(
        description="The payment that settled this row, in charges[]."
    )
    recorded_at: Timestamp
    updated_at: Timestamp | None


class CoverageSubscriber(BaseModel):
    """The plan holder, when it is not the client."""

    first_name: str | None
    last_name: str | None
    date_of_birth: date | None
    sex: str | None


class Coverage(BaseModel):
    """One insurance plan on the client's chart, current or replaced."""

    id: str
    payer_name: str | None = Field(description="The payer as the practice lists it.")
    payer_id: str | None = Field(description="The payer's electronic id, as printed on the card.")
    member_id: str
    group_number: str | None
    plan_name: str | None
    subscriber_relationship: str = Field(description="self, spouse, child or other.")
    subscriber: CoverageSubscriber | None = Field(
        description="Who holds the plan when the client does not."
    )
    active: bool = Field(description="The plan the practice bills today.")
    verified_at: Timestamp | None = Field(description="When eligibility was last checked.")
    created_at: Timestamp
    updated_at: Timestamp


class ClaimLineAdjustment(BaseModel):
    """One adjustment the payer applied to a line, as its remittance said."""

    group_code: str
    reason_code: str
    amount_cents: int


class ExportClaimLine(BaseModel):
    """One service on a claim: a code on a date for an amount."""

    id: str
    line_number: int
    service_date: date
    cpt: str
    modifiers: list[str]
    units: int
    charge_cents: int
    diagnosis_codes: list[str] = Field(description="The claim's codes this line points at.")
    telehealth: bool
    allowed_cents: int | None
    paid_cents: int
    patient_responsibility_cents: int | None
    adjustments: list[ClaimLineAdjustment]


class ClaimEvent(BaseModel):
    """One step in a claim's life, in the order it happened."""

    id: str
    kind: str = Field(description="submitted, ch_accepted, payer_accepted, rejected, and so on.")
    from_state: str | None
    to_state: str | None
    occurred_at: Timestamp


class ExportClaim(BaseModel):
    """One claim filed for the client, with its lines and its timeline."""

    id: str
    control_number: str
    state: str
    frequency_code: str = Field(description="1 original, 7 replacement, 8 void.")
    parent_claim_id: str | None = Field(description="The claim this one corrects or voids.")
    payer_name: str
    payer_id: str = Field(description="The payer's electronic id.")
    member_id: str
    place_of_service: str | None
    diagnosis_codes: list[str]
    total_charge_cents: int
    total_paid_cents: int
    submitted_at: Timestamp | None
    payer_accepted_at: Timestamp | None
    adjudicated_at: Timestamp | None
    payer_claim_number: str | None
    lines: list[ExportClaimLine]
    events: list[ClaimEvent] = Field(description="Oldest first.")
    created_at: Timestamp
    updated_at: Timestamp


class PatientExportDocument(BaseModel):
    """One client's chart as structured data (``patient.json``)."""

    schema_version: Literal["1.4"] = SCHEMA_VERSION
    exported_at: Timestamp
    options: ExportOptions
    patient: ExportPatient
    practitioner: Practitioner
    sessions: list[Encounter]
    standalone_notes: list[DocumentReference] = Field(
        description="Notes written without a session, such as an intake or narrative."
    )
    documents: list[ExportDocument] = Field(
        description="Files uploaded to the chart, each carried under documents/ in the archive."
    )
    appointments: list[ExportAppointment] = Field(description="Oldest first.")
    outcome_measures: list[Observation] = Field(description="Oldest first.")
    message_threads: list[ExportMessageThread] = Field(description="Oldest first.")
    medications: list[MedicationStatement] = Field(description="Active medications first.")
    diagnoses: list[Condition] = Field(description="Oldest first.")
    charges: list[ExportCharge] = Field(description="The ledger, oldest first.")
    coverage: list[Coverage] = Field(description="Every plan on the chart, newest first.")
    claims: list[ExportClaim] = Field(description="Every claim filed, oldest first.")


ManifestFileKind = Literal[
    "pdf",
    "json",
    "schema",
    "text",
    "document",
    "intake_form",
    "statement",
    "superbill",
    "csv",
    "archive",
]


class ManifestFile(BaseModel):
    path: str
    bytes: int
    sha256: str
    kind: ManifestFileKind


class ExportManifest(BaseModel):
    """``manifest.json``: every other file in the archive, with its checksum."""

    schema_version: Literal["1.4"] = SCHEMA_VERSION
    exported_at: Timestamp
    options: ExportOptions
    files: list[ManifestFile]
