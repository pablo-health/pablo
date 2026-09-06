# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQLAlchemy ORM models for the practice schema.

Each practice gets its own PostgreSQL schema containing these tables.
Models map 1:1 to the existing domain dataclasses but are database-aware.

Complex nested structures (SOAP notes, transcripts, EHR route steps) are
stored as JSONB — they're always read/written as a whole and rarely queried.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models.enums import ClinicianRole, OutcomeMeasureSource
from ..rules.enforcement import FlagBehavior, ItemStatus, RequirementLevel


class Base(DeclarativeBase):
    """Base class for all practice-schema ORM models."""


class ClinicianProfileRow(Base):
    __tablename__ = "clinician_profiles"

    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(50))
    credentials: Mapped[str | None] = mapped_column(String(100))
    # Structured credential titles; ``credentials`` is the joined display.
    credential_titles: Mapped[list | None] = mapped_column(JSONB)
    role: Mapped[str] = mapped_column(String(20), default="clinician")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    license_number: Mapped[str | None] = mapped_column(String(100))
    license_state: Mapped[str | None] = mapped_column(String(2))
    dea_number: Mapped[str | None] = mapped_column(String(50))
    npi_number: Mapped[str | None] = mapped_column(String(20))
    # NUCC health care provider taxonomy code (e.g. 101YM0800X) — the
    # specialty classification a payer expects on a claim's rendering-
    # provider loop, alongside npi_number. Free text so a clinician whose
    # specialty isn't in the picker can still enter one.
    taxonomy_code: Mapped[str | None] = mapped_column(String(10))


class PatientRow(Base):
    """Patient master record.

    Access (read/write) is governed by :class:`PatientClinicianRow`
    grants, not by a ``user_id`` column on the row itself. The column
    was dropped in migration ``9dea1edf7fe0`` once the
    ``patient_clinicians`` access table became the source of truth;
    the RLS policy on this table is ``has_patient_access(id,
    current_user)``.
    """

    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name_lower: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    last_name_lower: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Civil date (no time/tz). DB type is native DATE; the API speaks ISO
    # date strings, so the repository converts at the row boundary.
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    diagnosis: Mapped[str | None] = mapped_column(Text)
    session_count: Mapped[int] = mapped_column(Integer, default=0)
    last_session_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_session_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Soft-delete marker (THERAPY-nyb): NULL = live row; read paths omit
    # non-null rows. Core keeps soft-delete + audit only; hosted purge
    # (THERAPY-cgy) may remove clinical rows past retention after writing the
    # minimal retention stub in the compliance schema.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Chart closure (THERAPY-hek). Orthogonal to soft-delete: a closed
    # chart is a live, retained record whose care episode has ended.
    # ``status`` stays in {active, inactive, on_hold} — closure is a
    # timestamp, not a new status enum value, so the existing list
    # filters keep returning chart-closed patients (with these fields
    # visible). The hard-purge cron keys off ``deleted_at``, never off
    # ``chart_closed_at``.
    chart_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chart_closure_reason: Mapped[str | None] = mapped_column(Text)
    # Whether the patient has consented to receive protected health information
    # by email, and the provenance of that decision. Emailing a patient about
    # their care can disclose PHI over an external channel, so any deployment
    # that sends patient email needs to know whether email is a consented PHI
    # channel for this patient before including clinical detail.
    #
    # ``phi_email_consent`` is a NULLABLE boolean carrying three states: ``NULL``
    # = no record on file (never asked), ``True`` = consented, ``False`` =
    # declined. The current decision lives here; the grant/withdrawal *history*
    # lives in the audit trail (recording a change is an audited event). A
    # withdrawal is simply setting the flag back to ``False``.
    phi_email_consent: Mapped[bool | None] = mapped_column(Boolean)
    # When the consent decision was recorded / obtained.
    phi_email_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Optional reference to a signed consent document (e.g. a patient_document
    # id or storage path) backing the attestation. ``NULL`` when the consent was
    # recorded as a clinician attestation without an attached document.
    phi_email_consent_doc: Mapped[str | None] = mapped_column(Text)
    # The user who recorded the consent decision (audit provenance).
    phi_email_consent_by: Mapped[str | None] = mapped_column(String(128))
    # Per-patient rate override, integer minor units (cents). NULL = no
    # override; the effective rate falls through to the appointment type's
    # default (see app.scheduling_engine.services.rate_resolver). Reduced-fee
    # and sliding-scale arrangements are per-person, so this is a real
    # column rather than a note someone has to remember to read.
    rate_cents: Mapped[int | None] = mapped_column(Integer)
    # Free-text record of a sliding-scale arrangement, in the clinician's own
    # words. Never parsed or used in arithmetic — exists so the reason for a
    # rate survives staff turnover and the clinician's memory.
    sliding_scale_note: Mapped[str | None] = mapped_column(Text)
    # Where this row came from, for a human merge review to prioritize.
    # NULL = created by staff in the normal chart flow (the overwhelming
    # majority of rows, and not itself suspicious). A non-NULL value marks a
    # row created through an unauthenticated intake surface that cannot
    # verify the caller's claimed identity, so it may duplicate an existing
    # chart — 'voice' today, room for e.g. 'public_booking' later. Nothing
    # reads this column to merge or de-duplicate automatically; it only
    # flags a row for a person to look at.
    origin: Mapped[str | None] = mapped_column(String(20))
    # Mailing address, collected for claim submission (X12 837P subscriber/
    # patient loop). Optional — a chart with no billing intent has no reason
    # to require it.
    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(2))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    # X12 DMG03 administrative sex code set — what a claim's demographic
    # segment expects, not a gender-identity field. Displayed as "Sex on
    # insurance card" in the UI.
    sex: Mapped[str | None] = mapped_column(String(1))

    __table_args__ = (
        # Backs PatientRepository.find_by_email, whose `lower(email) = ?`
        # predicate cannot use a plain column index. Declared on the ORM
        # (not raw op.execute) so create_all emits it and every freshly
        # provisioned tenant gets it from tenant_template.sql.
        Index(
            "ix_patients_email_lower",
            func.lower(email),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("sex IN ('M', 'F', 'U')", name="ck_patients_sex"),
    )


class TherapySessionRow(Base):
    __tablename__ = "therapy_sessions"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    session_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    session_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    transcript: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Companion scheduling fields
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    video_link: Mapped[str | None] = mapped_column(Text)
    video_platform: Mapped[str | None] = mapped_column(String(30))
    session_type: Mapped[str | None] = mapped_column(String(30))
    duration_minutes: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audio_gcs_path: Mapped[str | None] = mapped_column(Text)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    # AssemblyAI transcript IDs for Cloud Task polling
    transcription_job_metadata: Mapped[dict | None] = mapped_column(JSONB)
    # PII-redacted transcript variants (note-side variants live on NoteRow).
    redacted_transcript: Mapped[str | None] = mapped_column(Text)
    naturalized_transcript: Mapped[str | None] = mapped_column(Text)
    # Soft-delete marker (THERAPY-nyb). NULL = live row; non-null hides the
    # session (and JSONB transcript payload) from normal reads.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NoteRow(Base):
    """Patient-owned clinical note (SOAP, DAP, narrative, etc.).

    Notes are first-class and patient-scoped. ``session_id`` is nullable so a
    note can exist without a recording (the standalone-note flow). When
    present, ``UNIQUE(session_id) WHERE session_id IS NOT NULL`` preserves
    today's 1:1 session↔note invariant. See pa-0nx (notes/sessions split).
    """

    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    note_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="soap", default="soap"
    )
    # AI-generated and clinician-edited note bodies. Shape varies by
    # note_type; the registry owns validation. Mirrors the existing
    # TherapySessionRow.note_content / note_content_edited columns.
    content: Mapped[dict | None] = mapped_column(JSONB)
    content_edited: Mapped[dict | None] = mapped_column(JSONB)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quality_rating: Mapped[int | None] = mapped_column(Integer)
    quality_rating_reason: Mapped[str | None] = mapped_column(Text)
    quality_rating_sections: Mapped[list | None] = mapped_column(JSONB)
    # 'processing' | 'complete' | 'failed' — see app.models.note.Note.status.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="complete", default="complete"
    )
    # Export tracking — mirrors TherapySessionRow.export_*
    export_status: Mapped[str] = mapped_column(String(20), default="not_queued")
    export_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    export_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    export_reviewed_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # PII-redacted variants (extension-tier).
    redacted_content: Mapped[dict | None] = mapped_column(JSONB)
    naturalized_content: Mapped[dict | None] = mapped_column(JSONB)
    redacted_export_payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Soft-delete marker (THERAPY-nyb). NULL = live row.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ux_notes_session_id",
            "session_id",
            unique=True,
            postgresql_where=text("session_id IS NOT NULL"),
        ),
        Index(
            "ix_notes_patient_finalized",
            "patient_id",
            "finalized_at",
            postgresql_using="btree",
        ),
    )


class PatientClinicianRow(Base):
    """Explicit per-(patient, clinician) access grants.

    Replaces ``patients.user_id`` as the source of truth for "which
    clinician(s) can read/write this patient's chart". v1 ships with
    one row per patient (``role = 'primary'``, backfilled from
    ``patients.user_id``); co-treating, supervision, and coverage
    rows are inserted as the corresponding workflows land.

    The CHECK on ``role`` mirrors :class:`ClinicianRole` — the
    module-level assertion below fails the import if they drift.
    """

    __tablename__ = "patient_clinicians"

    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default="primary",
        default=ClinicianRole.PRIMARY.value,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    granted_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "role IN ('primary', 'co_treating', 'supervisor', 'covering')",
            name="ck_patient_clinicians_role",
        ),
    )


# Fail-fast guard: the CHECK constraint string above and the enum must
# enumerate the same set. If a contributor adds a role to one without
# the other, this trips at import (and therefore in every test run)
# instead of silently allowing inserts the enum doesn't know about.
_MODEL_ROLE_VALUES = frozenset({"primary", "co_treating", "supervisor", "covering"})
_ENUM_ROLE_VALUES = frozenset(r.value for r in ClinicianRole)
if _MODEL_ROLE_VALUES != _ENUM_ROLE_VALUES:
    raise RuntimeError(
        f"ClinicianRole enum ({sorted(_ENUM_ROLE_VALUES)}) and the "
        f"patient_clinicians CHECK constraint "
        f"({sorted(_MODEL_ROLE_VALUES)}) have drifted. Update both."
    )


class OutcomeMeasureRow(Base):
    """Scored clinical instrument result (PHQ-9, GAD-7, or any generic instrument).

    One row per administration — a patient may have many rows for the same
    instrument over time. The trend-query index on
    ``(patient_id, instrument, administered_at)`` is the hot path for
    displaying score-over-time charts in the patient chart view.

    Access is governed by app-layer patient-access checks (the same
    ``has_patient_access`` function used by the notes table) — no separate
    row-level-security policy, matching how the notes table is protected.
    See PABLO-o5k.
    """

    __tablename__ = "outcome_measures"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    appointment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    # Short instrument code — 'phq9', 'gad7', etc. No CHECK constraint:
    # generic shape allows new instruments to be data, not schema changes.
    instrument: Mapped[str] = mapped_column(String(20), nullable=False)
    # null until is_complete (all items present) or an explicit total_score
    # is submitted without item_scores.
    total_score: Mapped[int | None] = mapped_column(Integer)
    # Per-item values e.g. {"1": 2, "2": 3, ...}.  Null when only a summary
    # total is recorded.
    item_scores: Mapped[dict | None] = mapped_column(JSONB)
    is_complete: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        default=False,
    )
    # Clinical provenance — who/what produced the scores.
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    # Reserved for future verbal-administration provenance (transcript spans).
    item_citations: Mapped[dict | None] = mapped_column(JSONB)
    administered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Soft-delete marker.  NULL = live row; non-null hides the row from
    # normal reads (list/get) but preserves the audit trail.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "source IN ('patient_self_report','clinician_administered_verbal','manual','inferred')",
            name="ck_outcome_measures_source",
        ),
        Index(
            "ix_outcome_measures_patient_instrument_administered",
            "patient_id",
            "instrument",
            "administered_at",
        ),
    )


class PatientMedicationRow(Base):
    """Per-patient medication record.

    Access governed by app-layer patient-access checks (same
    has_patient_access function as notes and outcome_measures) — no
    separate RLS policy needed.
    """

    __tablename__ = "patient_medications"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    drug_name: Mapped[str] = mapped_column(String(200), nullable=False)
    dose: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[date | None] = mapped_column(Date)
    stopped_at: Mapped[date | None] = mapped_column(Date)
    # Free-text reason the medication was stopped (e.g. ineffective, side
    # effects, remission). Only meaningful for discontinued rows; nullable.
    stop_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','discontinued','on_hold')",
            name="ck_patient_medications_status",
        ),
        Index(
            "ix_patient_medications_patient_status",
            "patient_id",
            "status",
        ),
    )


# Fail-fast guard: the CHECK constraint string above and OutcomeMeasureSource
# must enumerate the same set.  If a contributor adds a value to one without
# the other, this trips at import (and therefore in every test run) instead of
# silently allowing inserts the enum doesn't know about.
_MODEL_SOURCE_VALUES = frozenset(
    {"patient_self_report", "clinician_administered_verbal", "manual", "inferred"}
)
_ENUM_SOURCE_VALUES = frozenset(s.value for s in OutcomeMeasureSource)
if _MODEL_SOURCE_VALUES != _ENUM_SOURCE_VALUES:
    raise RuntimeError(
        f"OutcomeMeasureSource enum ({sorted(_ENUM_SOURCE_VALUES)}) and the "
        f"outcome_measures CHECK constraint "
        f"({sorted(_MODEL_SOURCE_VALUES)}) have drifted. Update both."
    )


class DiagnosticAssessmentRow(Base):
    """A structured diagnostic determination for a patient (PABLO-6xj).

    One row per assessment: the clinician's per-criterion responses + gate
    attestations against a versioned definition (snapshotted by
    ``definition_code`` + ``definition_version``), the computed
    ``meets_criteria`` (NULL for ``checklist`` definitions, which make no
    algorithmic determination), and the clinician-confirmed ICD-10-CM code. Distinct
    from ``outcome_measures`` (continuous symptom scores) — this is a
    point-in-time categorical determination.

    Per-tenant (lives in each ``practice_{id}`` schema), access governed by the
    app-layer ``has_patient_access`` function, same as ``notes`` /
    ``outcome_measures`` — no separate RLS policy.

    ``criterion_citations`` and ``confirmed_at`` are unused at launch; they are
    reserved for future provenance-tracked capture (which source supports each
    criterion, plus a clinician confirmation step), shipped now so that
    capability needs no migration.
    """

    __tablename__ = "diagnostic_assessments"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    appointment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    # Definition code + version snapshotted so the record reflects the rubric
    # as it was when the determination was made.
    instrument: Mapped[str] = mapped_column(String(40), nullable=False)
    definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    # {criterion_key: bool} and {gate_key: bool}
    criterion_responses: Mapped[dict] = mapped_column(JSONB, nullable=False)
    gate_responses: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Nullable: ``checklist`` definitions record responses but make no
    # algorithmic determination, so meets_criteria is NULL for those rows.
    meets_criteria: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Clinician-confirmed ICD-10-CM code, validated against the platform
    # icd10_codes catalog at write time. Null until confirmed.
    determined_icd10: Mapped[str | None] = mapped_column(String(10))
    diagnosis_label: Mapped[str | None] = mapped_column(String(120))
    # Per-criterion provenance (which source supports each criterion).
    # Unused at launch.
    criterion_citations: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    # Null = unconfirmed; set when a clinician confirms.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "source IN ('patient_self_report','clinician_administered_verbal','manual','inferred')",
            name="ck_diagnostic_assessments_source",
        ),
        Index(
            "ix_diagnostic_assessments_patient_instrument_assessed",
            "patient_id",
            "instrument",
            "assessed_at",
        ),
    )


class EhrPromptRow(Base):
    __tablename__ = "ehr_prompts"

    ehr_system: Mapped[str] = mapped_column(String(50), primary_key=True)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="")


class EhrRouteRow(Base):
    __tablename__ = "ehr_routes"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    ehr_system: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    route_name: Mapped[str] = mapped_column(String(255), nullable=False)
    steps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppointmentRow(Base):
    __tablename__ = "appointments"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Set only while status is 'pending': the instant the request stops holding
    # its slot. Indexed because the sweep that expires them is a range scan.
    pending_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    # SHA-256 of the confirmation token mailed to the booker on a hold from
    # a booking link that requires email confirmation. The raw token is
    # never stored — same hash-at-rest pattern as LaunchIntentStore.
    confirmation_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    #: Which ``appointment_types`` row this is an instance of.
    #:
    #: Nullable because an appointment can outlive its type: deleting a type
    #: sets this to NULL rather than refusing, since the appointment still
    #: happened and its record should survive the type being tidied away.
    #: ``session_type`` below keeps the name it was booked under, so history
    #: reads correctly even after the link is gone.
    appointment_type_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("appointment_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: The type's name as it stood when this was booked.
    #:
    #: Denormalised on purpose, and NOT redundant with the id above. A
    #: clinician can rename a type, and a past appointment should still read
    #: as what it was called at the time — the id says which type it is now,
    #: this says what it was called then.
    session_type: Mapped[str] = mapped_column(String(30), nullable=False)
    video_link: Mapped[str | None] = mapped_column(Text)
    video_platform: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
    # Registry key for the note generated when a session is started from this
    # appointment. Mirrors NoteRow.note_type.
    note_type: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="soap", default="soap"
    )
    # Recurrence
    recurrence_rule: Mapped[str | None] = mapped_column(String(50))
    recurring_appointment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    recurrence_index: Mapped[int | None] = mapped_column(Integer)
    is_exception: Mapped[bool] = mapped_column(Boolean, default=False)
    # Google Calendar sync
    google_event_id: Mapped[str | None] = mapped_column(String(255))
    google_calendar_id: Mapped[str | None] = mapped_column(String(255))
    google_sync_status: Mapped[str | None] = mapped_column(String(20))
    # iCal sync
    ical_uid: Mapped[str | None] = mapped_column(String(255))
    ical_source: Mapped[str | None] = mapped_column(String(50), index=True)
    ical_sync_status: Mapped[str | None] = mapped_column(String(20))
    ehr_appointment_url: Mapped[str | None] = mapped_column(Text)
    # Clinical link
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    # Billing codes for the visit — see app.scheduling_engine.models.appointment.
    # Clinician-entered only; every column is nullable and nothing here is
    # populated automatically.
    service_code: Mapped[str | None] = mapped_column(String(10))
    modifiers: Mapped[list | None] = mapped_column(JSONB)
    unit_count: Mapped[int | None] = mapped_column(Integer)
    place_of_service: Mapped[str | None] = mapped_column(String(2))
    diagnosis_codes: Mapped[list | None] = mapped_column(JSONB)
    # Reminders
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_1h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AvailabilityRuleRow(Base):
    __tablename__ = "availability_rules"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    enforcement: Mapped[str] = mapped_column(String(10), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppointmentTypeRow(Base):
    """A kind of appointment: how long it runs, who it is for, when it may be offered.

    This started as a fee table and is now the unit of scheduling. A type
    carries its own length and its own booking window, because a fifteen-minute
    consultation and a sixty-minute intake do not want the same notice, the
    same lead time, or the same horizon.

    ``default_fee_cents`` remains the fee absent a per-patient override — see
    :mod:`app.scheduling_engine.services.rate_resolver`.

    ``appointments.appointment_type_id`` references this table, so renaming a
    type no longer orphans the appointments booked under it. Two places
    deliberately still hold a plain string instead:

    * ``appointments.session_type`` — kept as the name the appointment was
      booked under, so history reads correctly after a rename.
    * ``booking_links.session_type`` — that table is PLATFORM-scoped, because
      a public slug has to resolve before any tenant schema can be selected.
      A platform table cannot hold a foreign key into one of N per-tenant
      schemas, so this one cannot be converted and should not be attempted.
    """

    __tablename__ = "appointment_types"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_fee_cents: Mapped[int | None] = mapped_column(Integer)

    #: How long this appointment runs. The practice-wide default in
    #: ``session_defaults`` still seeds new appointments; this is what the type
    #: itself is worth when times are proposed for it.
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="50")

    #: Who may be offered this type: ``new``, ``existing`` or ``both``. A
    #: consultation is for people who are not patients yet; a standard session
    #: is not.
    audience: Mapped[str] = mapped_column(String(10), nullable=False, server_default="existing")

    #: Least notice this type needs, in hours. ``None`` means "use the
    #: practice default" and is deliberately distinct from ``0``, which means
    #: "no notice required".
    min_notice_hours: Mapped[int | None] = mapped_column(Integer)

    #: How far out the first offerable day is, in working days. ``0`` allows
    #: same-day; ``1`` means "not today". Which days count comes from the
    #: availability rules, never from here.
    earliest_offer_business_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )

    #: How far ahead this type may be offered, in ``horizon_unit`` units.
    horizon: Mapped[int] = mapped_column(Integer, nullable=False, server_default="10")

    #: ``business`` counts only days the practice works; ``days`` is calendar
    #: days. "Ten business days" and "two weeks" are different promises.
    horizon_unit: Mapped[str] = mapped_column(String(10), nullable=False, server_default="business")

    #: Whether a patient may take a slot of this type themselves. Off by
    #: default: booking without the clinician in the loop is opt-in per type
    #: AND gated by the practice policy.
    self_bookable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")

    #: Whether Pablo may propose times for this type when it suggests times.
    #: On by default — a type that exists is normally one you want offered.
    offerable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")

    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_appointment_types_user_name"),
        CheckConstraint("duration_minutes BETWEEN 5 AND 480", name="ck_appointment_types_duration"),
        CheckConstraint(
            "audience IN ('new', 'existing', 'both')", name="ck_appointment_types_audience"
        ),
        CheckConstraint(
            "min_notice_hours IS NULL OR min_notice_hours >= 0",
            name="ck_appointment_types_min_notice",
        ),
        CheckConstraint(
            "earliest_offer_business_days >= 0", name="ck_appointment_types_earliest_offer"
        ),
        CheckConstraint("horizon > 0", name="ck_appointment_types_horizon"),
        CheckConstraint(
            "horizon_unit IN ('business', 'days')", name="ck_appointment_types_horizon_unit"
        ),
    )


class SchedulingPolicyRow(Base):
    """The practice's standing scheduling policy. One row per tenant.

    Answers the questions an appointment type does not: how late a patient may
    cancel, how a new enquiry starts, whether patients may book at all. A type
    says what an appointment IS; this says what the practice will allow to
    happen to its calendar.

    Singleton, pinned by ``CHECK (id = 1)``, so a save upserts the one row.

    Every gate defaults off or strict. ``self_book_existing`` and
    ``self_book_new`` are both false, and ``self_book_mode`` is ``request`` (a
    pending appointment the clinician confirms) rather than ``auto``. A
    practice upgrading into this code must not discover that patients can
    suddenly book it.

    Whether a PARTICULAR type may be self-booked lives on
    ``appointment_types.self_bookable``, not here. Two switches, deliberately:
    this one is the practice saying "self-booking is a thing I allow at all",
    the per-type one is "and this type in particular". Both must be on.

    Storing policy is all this does. Enforcing it at booking time is separate
    and not yet built.
    """

    __tablename__ = "scheduling_policy"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)

    #: Least notice for any new booking, in hours. A type may demand more via
    #: ``appointment_types.min_notice_hours``; none may demand less.
    min_notice_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    #: The furthest ahead anything may be booked, whatever a type says.
    max_horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    cancel_cutoff_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    reschedule_cutoff_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=24)
    #: How long a request-mode booking holds its slot before the sweep releases it.
    pending_hold_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=72)

    #: May existing patients book from the portal at all.
    self_book_existing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: May people who are not patients yet. A separate switch on purpose: it
    #: lets a stranger put a first appointment on the calendar, which is a
    #: different decision from letting a known patient rebook.
    self_book_new: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: ``request`` holds the slot pending confirmation; ``auto`` books it outright.
    self_book_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="request")

    #: How a new enquiry starts: ``consult`` offers a short call first,
    #: ``intake`` offers the full first appointment straight away.
    new_patient_flow: Mapped[str] = mapped_column(String(10), nullable=False, default="consult")
    #: How far before an intake the paperwork must be back, in hours.
    intake_forms_due_hours: Mapped[int] = mapped_column(Integer, nullable=False, default=48)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_scheduling_policy_singleton"),
        CheckConstraint(
            "self_book_mode IN ('request', 'auto')", name="ck_scheduling_policy_self_book_mode"
        ),
        CheckConstraint(
            "new_patient_flow IN ('consult', 'intake')",
            name="ck_scheduling_policy_new_patient_flow",
        ),
        CheckConstraint("min_notice_hours >= 0", name="ck_scheduling_policy_min_notice"),
        CheckConstraint("max_horizon_days > 0", name="ck_scheduling_policy_max_horizon"),
        CheckConstraint("cancel_cutoff_hours >= 0", name="ck_scheduling_policy_cancel_cutoff"),
        CheckConstraint(
            "reschedule_cutoff_hours >= 0", name="ck_scheduling_policy_reschedule_cutoff"
        ),
        CheckConstraint("pending_hold_hours > 0", name="ck_scheduling_policy_pending_hold"),
        CheckConstraint(
            "intake_forms_due_hours >= 0", name="ck_scheduling_policy_intake_forms_due"
        ),
    )


class PracticeBillingProfileRow(Base):
    """The practice's billing identity: the legal entity a claim is filed as.

    One row per tenant, singleton, pinned by ``CHECK (id = 1)`` — same shape
    as :class:`SchedulingPolicyRow`, and for the same reason: this is
    practice-level configuration, not a per-patient or per-user record, so
    there is no ``user_id`` / ``patient_id`` to scope it by.

    ``tax_id_encrypted`` holds the EIN or SSN at rest, AES-256-GCM encrypted
    the same way OAuth calendar tokens are (see
    ``app.services.token_encryption``). ``tax_id_last4`` is stored
    separately, in the clear, purely for display — a settings page can show
    "···· 1234" without ever decrypting the real value.
    """

    __tablename__ = "practice_billing_profile"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)

    legal_name: Mapped[str | None] = mapped_column(String(255))
    tax_id_encrypted: Mapped[str | None] = mapped_column(Text)
    tax_id_last4: Mapped[str | None] = mapped_column(String(4))
    tax_id_type: Mapped[str | None] = mapped_column(String(3))
    #: Type-2 (organization) NPI. Nullable — a solo practice bills under its
    #: rendering clinician's own NPI instead of requiring a separate one.
    billing_npi: Mapped[str | None] = mapped_column(String(20))
    address_line1: Mapped[str | None] = mapped_column(String(255))
    address_line2: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(2))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    phone: Mapped[str | None] = mapped_column(String(50))
    #: The practice's general inbox — where a payer or the clearinghouse
    #: writes about an enrollment. A shared address on purpose: enrollment
    #: correspondence belongs to the practice, never to one clinician.
    contact_email: Mapped[str | None] = mapped_column(String(255))
    #: The clearinghouse's id for this practice's provider record, set the
    #: first time the profile is complete enough to register. NULL until then.
    clearinghouse_provider_id: Mapped[str | None] = mapped_column(String(80))
    #: Run an eligibility check on its own whenever coverage lands (intake,
    #: chart save). Off leaves only the chart card's re-verify button.
    eligibility_auto_check: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_practice_billing_profile_singleton"),
        CheckConstraint(
            "tax_id_type IN ('ein', 'ssn')", name="ck_practice_billing_profile_tax_id_type"
        ),
    )


class GoogleCalendarTokenRow(Base):
    __tablename__ = "google_calendar_tokens"

    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    encrypted_tokens: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, server_default="google")
    write_target: Mapped[str] = mapped_column(String(32), nullable=False, server_default="primary")
    event_titling: Mapped[str] = mapped_column(String(16), nullable=False, server_default="generic")
    titling_attested_account: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    granted_capabilities: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default="push,import"
    )
    calendar_id: Mapped[str | None] = mapped_column(String(255))
    sync_token: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    consecutive_error_count: Mapped[int] = mapped_column(default=0)


class ICalClientMappingRow(Base):
    __tablename__ = "ical_client_mappings"

    doc_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    ehr_system: Mapped[str] = mapped_column(String(50), nullable=False)
    client_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ICalSyncConfigRow(Base):
    __tablename__ = "ical_sync_configs"

    doc_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    ehr_system: Mapped[str] = mapped_column(String(50), nullable=False)
    encrypted_feed_url: Mapped[str] = mapped_column(Text, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_error_count: Mapped[int] = mapped_column(default=0)


class ComplianceItemRow(Base):
    """Therapist compliance reminder (license, insurance, CAQH, etc.).

    Owned by the therapist, not patient-scoped — these are the clinician's
    own credentials and are not PHI. ``item_type`` is a free-form string so
    new categories (BAA expirations, CEU progress) can be added without a
    migration. ``due_date`` is nullable for items the user wants to track
    but hasn't filled in yet.
    """

    __tablename__ = "compliance_items"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ComplianceDocumentRow(Base):
    """Dormant data-model rail for the Phase 3 compliance vault.

    Will eventually back uploaded artifacts (license PDFs, malpractice
    declarations, CAQH attestations, BAAs) attached to a
    ``ComplianceItemRow``. Shipping the table now — without routes,
    storage wiring, or UI — means self-hosters won't need a forced
    schema migration when the vault product surface lands. ``storage_uri``
    is opaque (gs:// today, s3:// or local fs in self-host) so the
    storage backend can swap without a column change. ``document_type``
    is a free-form string for v1 to keep the schema flexible while the
    vault feature shape is still settling.
    """

    __tablename__ = "compliance_documents"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    compliance_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("compliance_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    uploaded_by_user_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), nullable=False, index=True
    )


class SupervisionRelationshipRow(Base):
    """Per-user supervision / oversight relationship — PHI-free.

    Models the regulatory relationships a clinician must keep current:
    physician delegation, NP collaborative agreements, PA supervision,
    and pre-licensure clinical supervision. These describe the
    clinician's own professional standing (and that of their named
    supervisor), not any patient, so the table lives in the practice
    schema alongside ``compliance_items`` and is gated by ``user_id``
    like the rest of the user-owned data.

    The relationship's review deadline rides an existing
    ``compliance_items`` row (``compliance_item_id``) so it reuses the
    reminder/dispatch machinery — ``next_review_date`` mirrors that
    item's ``due_date``. The link is nullable so a relationship can be
    recorded before its review item exists. ``relationship_type`` and
    ``status`` are free-form strings (validated at the service layer)
    to keep the schema flexible across professions and jurisdictions.
    """

    __tablename__ = "supervision_relationships"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    compliance_item_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("compliance_items.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    relationship_type: Mapped[str] = mapped_column(String(50), nullable=False)
    supervisor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    supervisor_credential: Mapped[str | None] = mapped_column(String(100))
    supervisor_dea: Mapped[str | None] = mapped_column(String(50))
    supervisor_license: Mapped[str | None] = mapped_column(String(100))
    state: Mapped[str | None] = mapped_column(String(2))
    effective_date: Mapped[date | None] = mapped_column(Date)
    review_cadence_days: Mapped[int | None] = mapped_column(Integer)
    next_review_date: Mapped[date | None] = mapped_column(Date)
    authority_ref: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SupervisionHoursRow(Base):
    """Accrued-hour log entry against a supervision relationship — PHI-free.

    Backs pre-licensure supervision (associate/intern hour requirements),
    where the clinician logs direct/indirect hours toward a board total.
    Each entry belongs to a ``SupervisionRelationshipRow`` (cascade
    delete) and carries ``user_id`` directly so it follows the same
    user-isolation policy as the rest of the user-owned tables. ``hours``
    is stored as an exact decimal so fractional logging (0.25, 1.5)
    sums cleanly. ``kind`` is a free-form string (direct | indirect).
    """

    __tablename__ = "supervision_hours"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    supervision_relationship_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("supervision_relationships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    logged_date: Mapped[date] = mapped_column(Date, nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    supervisor: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChatConversationRow(Base):
    """Patient-context chat conversation envelope (THERAPY-bhv).

    Lives in the practice schema alongside ``patients``. No ``tenant_id``
    column — schema-per-practice already isolates rows. ``patient_id``
    and ``caller_system_prompt`` are immutable after insert; the service
    layer enforces this (no DB constraint because the audit guarantee is
    a service-level invariant, not a schema invariant).

    Cascade delete on the parent: removing a conversation drops its
    messages via the FK below. See chat-design doc §6.6 for the
    user-initiated hard-delete semantics.
    """

    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    owner_user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    caller_system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    caller_feature_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    default_source_selection: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_turn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_chat_conversations_patient_last_turn",
            "patient_id",
            "last_turn_at",
        ),
        Index(
            "ix_chat_conversations_owner_last_turn",
            "owner_user_id",
            "last_turn_at",
        ),
    )


class ChatMessageRow(Base):
    """A single turn (user or assistant) inside a ChatConversation.

    Append-only. ``sequence`` is monotonic per conversation starting at 1.
    Per design doc §10.4 the per-turn forensic detail (content, manifest,
    token counts) lives here, not in the audit log — keeping the audit
    table PHI-free and small.
    """

    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("chat_conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_selection: Mapped[dict | None] = mapped_column(JSONB)
    context_manifest: Mapped[dict | None] = mapped_column(JSONB)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    llm_model: Mapped[str | None] = mapped_column(String(128))
    llm_finish_reason: Mapped[str | None] = mapped_column(String(32))
    llm_error: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "ux_chat_messages_conversation_sequence",
            "conversation_id",
            "sequence",
            unique=True,
        ),
    )


class LlmUsageRow(Base):
    """Monthly LLM usage roll-up (THERAPY-f6eg, Phase 3b of THERAPY-bhv).

    Per design doc §11.6, aggregated by
    ``(user_id, feature_key, period_yyyymm, model)``. No ``tenant_id``
    column — schema-per-practice isolates rows, same as the chat tables.
    ``LlmUsageMeter.record_turn`` upserts; ``get_period_usage`` reads.
    """

    __tablename__ = "llm_usage"

    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    period_yyyymm: Mapped[str] = mapped_column(String(6), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    turn_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    first_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_llm_usage_period", "period_yyyymm"),
        Index("ix_llm_usage_feature_period", "feature_key", "period_yyyymm"),
    )


class PatientDocumentRow(Base):
    """Clinician-uploaded patient document (THERAPY-ak6m.2).

    Per-tenant table. RLS shape combines two policies, keyed on the
    ``category`` enum (see :class:`app.models.DocumentCategory` for
    the regulatory rationale):

    * ``chart`` rows follow the same patient-access model as
      :class:`NoteRow`: anyone with a ``patient_clinicians`` grant on
      the patient can see them. Default. Matches clinical reality —
      co-treating clinicians share the chart.
    * ``therapist_private`` and ``psychotherapy_notes`` rows collapse
      to direct ``user_id`` ownership: only the uploader can see
      them. Access predicate is identical for the two categories;
      they're kept distinct so downstream disclosure workflows
      (release-of-records, patient right-of-access) can filter on
      the HIPAA-meaningful boundary later.

    See :func:`app.db.enable_rls_on_schema` for the policy body.

    Lifecycle:

    * ``finalized_at`` is NULL between init (signed URL minted +
      placeholder row inserted) and finalize (GCS object verified +
      PyMuPDF extraction run). List/get filters
      ``finalized_at IS NOT NULL`` so abandoned init rows never appear.
    * ``extracted_text`` is NULL when PyMuPDF returned <100 chars
      (treated as a scanned PDF; ak6m.2.3 will OCR these).
    * ``deleted_at`` non-NULL = soft-deleted; GCS-object cleanup cron
      is deferred to ak6m.2.1.
    """

    __tablename__ = "patient_documents"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    gcs_path: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text)
    # Which extractor produced extracted_text:
    # "pymupdf" (native PDF text), "document_ai" (OCR), "unavailable"
    # (OCR attempted and failed). NULL until finalize.
    extracted_via: Mapped[str | None] = mapped_column(String(32))
    # OCR diagnostics (page_count, avg_confidence, low_confidence_pages,
    # latency_ms). JSONB so adding fields doesn't need a migration.
    extraction_metadata: Mapped[dict | None] = mapped_column(JSONB)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    # Access + disclosure classification. Set at init, immutable.
    # Stored as VARCHAR + CHECK (not a native PG enum) so future value
    # changes / table splits stay cheap. See DocumentCategory in
    # app/models/patient_document.py for the regulatory boundaries.
    category: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text("'chart'"),
        default="chart",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL = extracted synchronously under the old finalize path (read as
    # COMPLETE, see app.models.patient_document.ExtractionStatus). New rows
    # get an explicit value: 'pending' the moment the finalize worker job is
    # enqueued, then 'complete' or 'failed' once the worker finishes.
    extraction_status: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (
        Index("ix_patient_documents_patient_deleted", "patient_id", "deleted_at"),
        CheckConstraint(
            "category IN ('chart', 'consent', 'therapist_private', 'psychotherapy_notes')",
            name="ck_patient_documents_category",
        ),
        CheckConstraint(
            "extraction_status IS NULL OR extraction_status IN ('pending', 'complete', 'failed')",
            name="ck_patient_documents_extraction_status",
        ),
        CheckConstraint(
            "extracted_via IS NULL OR extracted_via IN ('pymupdf', 'document_ai', 'unavailable')",
            name="ck_patient_documents_extracted_via",
        ),
    )


class AuditLogRow(Base):
    """HIPAA audit log entry.

    Schema is intentionally PHI-free: IDs only, no denormalized names or
    emails. The `changes` JSONB stores field-name diffs (not values) and
    non-PHI structured data like counts and enum transitions. Routine
    log-review jobs can query this table directly without a sanitizing view.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Actor identifier as recorded — kept VARCHAR, not native uuid. An audit
    # row must capture the event even when the actor isn't a clean uuid4
    # (system/service actions, legacy ids, a probe logged precisely because it
    # was unauthenticated); a uuid column would reject those at INSERT and lose
    # the record. Same "identifier as recorded" rationale as resource_id below.
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    # What KIND of principal ``user_id`` names. Both a clinician id and a
    # patient id are uuids, so without this a row cannot answer "clinician or
    # patient?" without joining two tables and hoping exactly one matches —
    # and this is the six-year record, read years later by someone in a
    # dispute. Server default 'clinician' so every existing row, and every
    # caller that does not set it, keeps the meaning it already had.
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="clinician")
    # For ``actor_type = 'system'``: which part of the system acted (a cron, a
    # queue worker, a background agent). NULL for every human kind, whose actor
    # is already named by ``user_id``. Free-form on purpose — a new background
    # job should not need a migration to be able to audit itself — so it is
    # neither constrained nor indexed as an enum would be.
    actor_component: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # resource_id is polymorphic (patient_id | session_id | user_id | …) —
    # stays String since it holds Firebase uids for user-resource actions.
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    patient_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    session_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        # Disarming a principal sets its GUC to '' rather than dropping it, so
        # every request runs with one of the two identity GUCs empty. Every
        # other principal column in the schema is a uuid, where '' fails the
        # cast and the comparison is a no-match; this one is VARCHAR for the
        # reasons above it, so '' is storable and '' = '' is true. Without
        # this constraint the empty id is a bucket shared by every principal
        # whose other GUC is cleared — readable and writable across the
        # clinician/patient boundary, invisible to legitimate readers, and
        # unreachable by the retention purge.
        CheckConstraint("user_id <> ''", name="audit_logs_user_id_not_empty"),
    )


# ---------------------------------------------------------------------------
# Prescribing encounter context (prescribing rules-engine input)
# ---------------------------------------------------------------------------
# The encounter + prescription record the prescribing rules engine evaluates:
# the facts a reviewer (state board peer expert, DEA, malpractice expert)
# checks a controlled-substance prescribing decision against. One encounter
# has zero or more prescriptions; the engine evaluates each prescription
# against the encounter context (state, modality, prior in-person, ...).
#
# ``schedule`` and ``drug_class`` are the engine's vocabulary — they mirror
# the rules-engine ``RuleContext`` dimensions, and rulesets gate items on
# exactly these tokens. Exposed as module constants so downstream callers
# (the enforcement evaluator, request schemas) share one source of truth; the
# CHECK constraints below are built from them so a token can't drift between
# the constant and the database.

PRESCRIPTION_SCHEDULES: tuple[str, ...] = ("II", "III", "IV", "V", "none")
PRESCRIPTION_DRUG_CLASSES: tuple[str, ...] = (
    "opioid",
    "stimulant",
    "benzodiazepine",
    "buprenorphine",
    "other",
)
ENCOUNTER_MODALITIES: tuple[str, ...] = (
    "in_person",
    "audio_video",
    "audio_only",
    "async",
)
ENCOUNTER_STATUSES: tuple[str, ...] = ("open", "finalized", "voided")


def _sql_in_list(values: tuple[str, ...]) -> str:
    """Render a tuple of tokens as a SQL ``IN (...)`` list literal."""

    return ", ".join(f"'{value}'" for value in values)


class PrescribingEncounterRow(Base):
    """A controlled-substance prescribing encounter — the rules-engine input.

    One row per prescribing visit, sibling of ``notes`` / ``diagnostic_assessments``
    inside each ``practice_{id}`` schema. Access is enforced at the application
    layer via ``has_patient_access`` (keyed on ``patient_id``), same as the
    rest of the per-patient chart — no separate RLS policy.

    Prescriber credentials and the delegating physician are **snapshotted**
    here (not only referenced) so the record reflects what was true at
    prescribing time — contemporaneous capture, no divergence if the standing
    ``clinician_profiles`` / ``supervision_relationships`` rows later change.
    ``delegation_ref`` points at the delegation agreement in force (e.g. a
    ``supervision_relationships`` row).

    Stamped with ``ruleset_version`` (the ruleset in force, e.g.
    ``"MI-RX-2026.06"``) so the rules applied to the encounter can be
    reconstructed later. ``status`` / ``finalized_at`` back the finalization
    gating added by the enforcement evaluator (layer 3); they are shipped now
    so that capability needs no later migration.

    The enforcement evaluator (layer 3) assembles a flat evaluation context
    from these columns; the curated ruleset ``trigger`` / ``satisfied_when``
    field paths resolve as:

    * ``prescription.{schedule,drug_class,days_supply,refills,quantity,strength}``
      -> :class:`PrescriptionRow`
    * ``context.{state,modality,prior_in_person,patient_in_sud_program}`` -> here
    * ``context.indication`` -> ``PrescriptionRow.indication``
    * ``context.first_in_course`` -> ``PrescriptionRow.first_in_course``
    * ``prescriber.{type,dea,license,npi}`` -> here
    * ``prescriber.delegation_status`` = ``"delegated"`` when
      ``delegation_ref`` is set
    """

    __tablename__ = "prescribing_encounters"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prescriber_user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    # Prescriber credentials, snapshotted at prescribing time. The standing
    # values live on clinician_profiles; the record must reflect what was true
    # when the script was written.
    prescriber_type: Mapped[str | None] = mapped_column(String(40))
    prescriber_npi: Mapped[str | None] = mapped_column(String(20))
    prescriber_dea: Mapped[str | None] = mapped_column(String(50))
    prescriber_license: Mapped[str | None] = mapped_column(String(100))
    # Pointer to the delegation agreement in force (e.g. a
    # supervision_relationships row); the delegating physician's name + DEA are
    # snapshotted alongside so the dual-DEA record is contemporaneous.
    delegation_ref: Mapped[str | None] = mapped_column(String(128))
    delegating_physician_name: Mapped[str | None] = mapped_column(String(255))
    delegating_physician_dea: Mapped[str | None] = mapped_column(String(50))
    # Encounter context — rules-engine RuleContext dimensions + triggers.
    state: Mapped[str | None] = mapped_column(String(2))
    modality: Mapped[str | None] = mapped_column(String(20))
    prior_in_person: Mapped[bool | None] = mapped_column(Boolean)
    patient_in_sud_program: Mapped[bool | None] = mapped_column(Boolean)
    # Whether this prescriber operates under a supervisory/collaborative
    # (delegation) agreement for this encounter — snapshotted at create from
    # the prescriber's standing credentials. ``False`` means an independent
    # prescriber, so delegation-only ledger items don't apply; ``NULL`` (legacy
    # rows, or no credential signal) preserves the ruleset's default behavior.
    requires_delegation: Mapped[bool | None] = mapped_column(Boolean)
    # The ruleset version in force, stamped when the encounter is evaluated /
    # finalized (e.g. "MI-RX-2026.06"). Null until then.
    ruleset_version: Mapped[str | None] = mapped_column(String(40))
    # The prescriber's clinical reasoning for the decision — free text in the
    # clinician's own words, written while the encounter is open. The system
    # may scaffold/prompt it but never machine-populates it. Part of what the
    # integrity digest commits to, so it is frozen with the rest at signing.
    clinical_reasoning: Mapped[str | None] = mapped_column(Text)
    # open -> finalized | voided. Finalization gating is layer 3; the column
    # ships now so that flow needs no later migration.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    encountered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The prescriber who finalized (signed) the encounter, and their attestation
    # statement — the human signature of the decision record. Both null until
    # finalization; the statement is the clinician's own words, never machine-
    # generated, and is part of what the integrity digest commits to.
    finalized_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    attestation_statement: Mapped[str | None] = mapped_column(Text)
    # Tamper-evident content digest of the finalized encounter snapshot — the
    # genesis link of the addendum hash chain. Null until finalization.
    integrity_digest: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_in_list(ENCOUNTER_STATUSES)})",
            name="ck_prescribing_encounters_status",
        ),
        CheckConstraint(
            f"modality IS NULL OR modality IN ({_sql_in_list(ENCOUNTER_MODALITIES)})",
            name="ck_prescribing_encounters_modality",
        ),
        Index(
            "ix_prescribing_encounters_patient_encountered",
            "patient_id",
            "encountered_at",
        ),
    )


class PrescriptionRow(Base):
    """A single prescription within a :class:`PrescribingEncounterRow`.

    The unit the rules engine evaluates: ``schedule`` + ``drug_class`` select
    which ruleset items apply (a Schedule II stimulant triggers the
    delegation / dual-DEA / MAPS items; a non-controlled drug, ``schedule
    "none"``, triggers nothing), and the quantitative fields
    (``days_supply``, ``refills``) drive the conditional triggers and
    ``satisfied_when`` checks. ``patient_id`` is denormalized from the
    encounter so per-tenant patient-access checks and chart queries key on it
    directly, same as ``notes`` / ``patient_medications``.
    """

    __tablename__ = "prescriptions"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    encounter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("prescribing_encounters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rxnorm_id: Mapped[str | None] = mapped_column(String(20))
    drug_name: Mapped[str | None] = mapped_column(String(200))
    schedule: Mapped[str] = mapped_column(String(4), nullable=False)
    drug_class: Mapped[str] = mapped_column(String(20), nullable=False)
    strength: Mapped[str | None] = mapped_column(String(100))
    quantity: Mapped[int | None] = mapped_column(Integer)
    days_supply: Mapped[int | None] = mapped_column(Integer)
    refills: Mapped[int] = mapped_column(Integer, nullable=False)
    # Conditional-rule triggers: indication (e.g. "acute_pain" -> the 7-day
    # acute-opioid limit) and whether this is the first prescription in a
    # course (-> Start Talking consent). Null = not asserted.
    indication: Mapped[str | None] = mapped_column(String(40))
    first_in_course: Mapped[bool | None] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            f"schedule IN ({_sql_in_list(PRESCRIPTION_SCHEDULES)})",
            name="ck_prescriptions_schedule",
        ),
        CheckConstraint(
            f"drug_class IN ({_sql_in_list(PRESCRIPTION_DRUG_CLASSES)})",
            name="ck_prescriptions_drug_class",
        ),
    )


class PrescribingEncounterAddendumRow(Base):
    """A dated, labelled correction appended to a finalized encounter.

    Finalized encounters are immutable; the only lawful change is an
    addendum. Addenda are append-only — no ``updated_at`` / ``deleted_at`` —
    and form a tamper-evident hash chain: ``digest`` is the content digest of
    this addendum and ``prev_digest`` links to the previous chain link (the
    encounter's ``integrity_digest`` for the first addendum, the prior
    addendum's chain link thereafter), so removing or reordering any addendum
    breaks every digest after it. Per-tenant, patient-scoped (RLS via
    ``has_patient_access``), same as the encounter.

    ``label`` is the kind of correction (clinician-supplied); ``text`` is the
    correction itself, in the clinician's own words. ``created_at`` is the
    server clock at the time of writing — backdating is not representable.
    """

    __tablename__ = "prescribing_encounter_addenda"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    encounter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("prescribing_encounters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Tamper-evident chain: digest of this addendum's content; prev_digest
    # links to the prior link (encounter.integrity_digest, then each prior
    # addendum's chain digest).
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    prev_digest: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# The checklist-ledger value vocabularies mirror the rules-engine enforcement
# enums (``app.rules.enforcement``) so a token can't drift between the engine
# that computes a status and the column that stores it; the CHECK constraints
# below are built from these tuples.
CHECKLIST_ITEM_STATUSES: tuple[str, ...] = tuple(s.value for s in ItemStatus)
CHECKLIST_FLAG_BEHAVIORS: tuple[str, ...] = tuple(f.value for f in FlagBehavior)
CHECKLIST_REQUIREMENT_LEVELS: tuple[str, ...] = tuple(r.value for r in RequirementLevel)


class PrescribingChecklistItemRow(Base):
    """The attestation ledger — one row per applicable rule item on an encounter.

    The verification record behind "no checkbox without evidence": when the
    enforcement evaluator (``app.rules.enforcement.evaluate_enforcement``) runs
    a curated ruleset against an open encounter + prescription, the
    attestation service (``app.prescribing.attestation``) persists one row here
    for each *applicable* item — its computed ``status``, its ``flag_behavior``
    / ``requirement_level``, and (once bound) the ``evidence_link`` that
    satisfies it. An item is ``satisfied`` only when its evidence resolves (or
    a computed ``satisfied_when`` check holds); a bare row with no evidence
    stays ``missing``. Items that stop applying (the drug changed) are
    soft-deleted, never silently flipped.

    ``ruleset_version`` records the ruleset in force when the row was computed,
    so the rules applied to the encounter can be reconstructed later — the same
    contemporaneous-capture guarantee the encounter itself carries. The ledger
    is mutable only while the encounter is ``open``; once finalized the
    encounter (and its ledger) are frozen and corrections become dated addenda.

    Per-tenant (each ``practice_{id}`` schema), patient-scoped: the
    ``patient_id`` column gives it the auto-applied ``has_patient_access`` RLS
    policy, same as the encounter and the rest of the per-patient chart.
    """

    __tablename__ = "prescribing_checklist_items"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    encounter_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("prescribing_encounters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalized from the encounter so per-tenant patient-access (RLS) and
    # chart queries key on it directly, same as prescriptions / addenda.
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The ruleset item id this row tracks (e.g. "mi_maps_review"). Unique per
    # encounter so re-running the evaluator upserts rather than duplicates.
    item_id: Mapped[str] = mapped_column(String(120), nullable=False)
    requirement_level: Mapped[str] = mapped_column(String(20), nullable=False)
    flag_behavior: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # The evidence that satisfies the item — a pointer to a real record (or a
    # signed clinician statement). Null = not yet bound; the item stays missing.
    evidence_link: Mapped[str | None] = mapped_column(String(512))
    # Who bound the evidence / attested, and when (server clock — no backdating).
    captured_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Free-form citation snapshotted from the rule item (statute / regulation).
    authority_ref: Mapped[str | None] = mapped_column(String(255))
    # The ruleset version in force when this row was computed (e.g.
    # "MI-RX-2026.06"), stamped so the rules applied can be reconstructed.
    ruleset_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "encounter_id",
            "item_id",
            name="uq_prescribing_checklist_items_encounter_item",
        ),
        CheckConstraint(
            f"status IN ({_sql_in_list(CHECKLIST_ITEM_STATUSES)})",
            name="ck_prescribing_checklist_items_status",
        ),
        CheckConstraint(
            f"flag_behavior IN ({_sql_in_list(CHECKLIST_FLAG_BEHAVIORS)})",
            name="ck_prescribing_checklist_items_flag_behavior",
        ),
        CheckConstraint(
            f"requirement_level IN ({_sql_in_list(CHECKLIST_REQUIREMENT_LEVELS)})",
            name="ck_prescribing_checklist_items_requirement_level",
        ),
    )


# ---------------------------------------------------------------------------
# Self-pay card payments
# ---------------------------------------------------------------------------

#: Ledger statuses for a card charge. ``pending`` is written before the card
#: processor is called at all; the outcome moves the row to ``succeeded`` or
#: ``failed``; a refund the practice issues from its own Stripe dashboard
#: arrives by webhook as ``refunded``. A ``failed`` row STAYS failed —
#: retrying is a fresh charge the clinician explicitly asks for, never an
#: automatic one. ``disputed`` is a chargeback the cardholder's bank raised —
#: NOT a refund, because it is not yet decided: it resolves to ``succeeded``
#: (the practice keeps the money) or ``dispute_lost`` (it does not).
CHARGE_STATUSES: tuple[str, ...] = (
    "pending",
    "succeeded",
    "failed",
    "refunded",
    "disputed",
    "dispute_lost",
)

#: The currency the ledger column defaults to. It exists so a row records what
#: the processor was actually told, not so a caller can pick one per charge.
DEFAULT_CHARGE_CURRENCY = "usd"


class PatientPaymentMethodRow(Base):
    """The card a practice keeps on file for one client — processor ids only.

    A card number must never be able to reach this database, and that is a
    property of the schema rather than of the routes above it: there is no
    column here a PAN or CVC could be written into. The browser posts the card
    straight to Stripe against a SetupIntent and hands the backend a ``pm_…``
    id; what is stored is that id, the customer id, and the display triple the
    UI renders ("Visa ···· 4242, exp 4/2029").

    One row per client (``patient_id`` is unique). Re-running setup replaces
    the card in place rather than accumulating stale ones the clinician would
    then have to choose between; several cards per client is a client-portal
    concern, not a charge-for-the-session one.

    ``stripe_payment_method_id`` is nullable for exactly one window: the row is
    created when the SetupIntent is minted (the customer id is known then) and
    completed when the browser confirms and Stripe reports which payment method
    got attached. A row with a NULL payment-method id is a setup that was
    started and never finished — it is not chargeable, and the charge route
    treats it as "no card on file".
    """

    __tablename__ = "patient_payment_methods"
    __table_args__ = (Index("ux_patient_payment_methods_patient_id", "patient_id", unique=True),)

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # Native uuid so the schema's ``has_patient_access`` policy applies
    # directly, the same way it does for notes and outcome measures.
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)

    # Stripe ids only. WHICH Stripe account they belong to is a deployment
    # question answered by ``app.payments.provider``, not recorded here.
    stripe_customer_id: Mapped[str] = mapped_column(String(255), nullable=False)
    stripe_payment_method_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # The display triple, straight off Stripe's ``card`` object. These are the
    # only card-shaped values that may ever be stored.
    card_brand: Mapped[str | None] = mapped_column(String(32), nullable=True)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    card_exp_month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    card_exp_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    # Which clinician put the card on file. Deliberately NOT named ``user_id``:
    # ``enable_rls_on_schema`` checks for ``user_id`` BEFORE ``patient_id``, so
    # that name would silently swap the patient-access policy for direct
    # ownership — the clinician who took the payment would be the only one who
    # could ever see the row, and a covering clinician would not. This column
    # records who acted, not who owns the row.
    created_by_user_id: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PatientChargeRow(Base):
    """One charge attempt against a client's card on file — the ledger row.

    Written FIRST, as ``pending``, before Stripe is called. That ordering is
    the point of the table: if the call times out, or the process dies between
    the call and the response, the practice still has a row saying a charge was
    attempted, and the webhook (or a human reading the Stripe dashboard) can
    reconcile it by ``stripe_payment_intent_id``. A ledger written only on
    success would lose exactly the cases somebody needs to look at.

    ``appointment_id`` is nullable: a charge need not hang off an appointment (a
    late-cancellation fee, or a balance), and an appointment can be deleted
    while the money record must not be. Soft reference, no foreign key.
    """

    __tablename__ = "patient_charges"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_in_list(CHARGE_STATUSES)})",
            name="ck_patient_charges_status",
        ),
        # Money is integer minor units and a charge is for a positive amount; a
        # refund is a status transition on this row, never a negative charge.
        CheckConstraint("amount_cents > 0", name="ck_patient_charges_amount_positive"),
        # The ledger read is "this client's charges, newest first".
        Index("ix_patient_charges_patient_created", "patient_id", "created_at"),
        # The webhook finds its row by the PaymentIntent id; unique so a
        # replayed event can never fan out across two rows. Partial, because
        # many rows legitimately sit at NULL between the insert and the call.
        Index(
            "ux_patient_charges_payment_intent",
            "stripe_payment_intent_id",
            unique=True,
            postgresql_where=text("stripe_payment_intent_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # Native uuid so the schema's ``has_patient_access`` policy applies.
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)

    # Soft reference to ``appointments.id``, nullable — see the docstring.
    appointment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)

    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default=DEFAULT_CHARGE_CURRENCY
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Stripe's machine-readable reason the attempt ended where it did — the
    # ``decline_code`` when there is one (``insufficient_funds``), else the
    # coarser ``code`` (``card_declined``). A token, never prose and never
    # clinical content; the UI maps it to copy on its side.
    status_detail: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Which clinician asked for the charge. NOT ``user_id`` — see
    # ``PatientPaymentMethodRow.created_by_user_id``.
    created_by_user_id: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # What the practice actually receives, straight off the charge's balance
    # transaction: ``fee_cents`` is what the processor kept, ``net_cents`` is
    # ``amount_cents - fee_cents``. Both NULL until the webhook receiver sees
    # the balance transaction — for some payment methods it settles slightly
    # after the charge succeeds, so NULL here means "not known yet", never
    # zero.
    fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    net_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)


#: Where a practice stands with a payer for electronic transactions. ``none``
#: is the default for a payer typed in from a client's card; the rest are
#: written by whatever files and tracks the enrollment.
PAYER_ENROLLMENT_STATUSES: tuple[str, ...] = ("none", "filed", "pending", "active", "error")

#: Who the coverage's subscriber is relative to the client. ``self`` is the
#: common case; anything else means the subscriber's own name and details
#: have to be on file before a claim can name them.
SUBSCRIBER_RELATIONSHIPS: tuple[str, ...] = ("self", "spouse", "child", "other")

#: Deadline defaults a payer row is created with, in days. These are the
#: common floor; a practice's participation agreement can say otherwise, and
#: the settings form lets it. Medicare's longer filing window is applied by
#: ``app.services.payer_defaults`` when the row is created, not here.
DEFAULT_TIMELY_FILING_DAYS = 90
DEFAULT_CORRECTED_CLAIM_DAYS = 90
DEFAULT_APPEAL_DAYS = 180


class PayerRow(Base):
    """An insurance payer this practice files with or checks eligibility against.

    Practice-level, not per-client: one row per payer, referenced by every
    coverage on file that names it. There is no ``user_id`` / ``patient_id``
    to scope it by, so — like ``practice_billing_profile`` — its isolation
    boundary is the tenant schema and RLS is deliberately left off
    (registered in ``_CORE_NOT_ROW_SCOPED``).

    ``payer_id`` is the electronic payer id printed on the card or listed in
    the clearinghouse's payer directory. ``clearinghouse_payer_id`` is the
    clearinghouse's own identifier for the same payer, filled in later by
    whatever looks the payer up; NULL until then.

    Behavioral benefits are often administered by a different entity than
    the one the medical card names. ``is_carveout`` marks such a payer and
    ``carveout_of`` points at the medical payer it carves out from, so a
    claim can be routed to the right one.

    The three ``*_days`` columns are the deadlines a claim against this payer
    lives under: how long after the service the original claim may be filed,
    how long after a rejection a corrected claim may follow, and how long
    after a denial an appeal may be lodged. Nothing here computes a date from
    them; the claim workflow reads them.
    """

    __tablename__ = "payers"
    __table_args__ = (
        CheckConstraint(
            f"enrollment_status IN ({_sql_in_list(PAYER_ENROLLMENT_STATUSES)})",
            name="ck_payers_enrollment_status",
        ),
        CheckConstraint("timely_filing_days > 0", name="ck_payers_timely_filing_days"),
        CheckConstraint("corrected_claim_days > 0", name="ck_payers_corrected_claim_days"),
        CheckConstraint("appeal_days > 0", name="ck_payers_appeal_days"),
        Index("ix_payers_payer_id", "payer_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    payer_id: Mapped[str] = mapped_column(String(80), nullable=False)
    clearinghouse_payer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    is_carveout: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    carveout_of: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("payers.id", ondelete="SET NULL"), nullable=True
    )
    enrollment_status: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    timely_filing_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_TIMELY_FILING_DAYS
    )
    corrected_claim_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_CORRECTED_CLAIM_DAYS
    )
    appeal_days: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_APPEAL_DAYS)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PatientCoverageRow(Base):
    """The insurance plan a client is on — what a claim and an eligibility
    check both need before they can be built.

    Carries ``patient_id`` and no ``user_id``, so ``enable_rls_on_schema``
    attaches the standard ``has_patient_access`` policy: only a clinician
    with a grant on the client can read or write their coverage.

    One active primary coverage per client (partial unique index on
    ``patient_id WHERE active``). Replacing a plan deactivates the old row
    rather than deleting it, so a claim filed under the old plan can still
    be read against what was on file at the time.

    The member id is protected health information, not a secret: it is
    stored as typed and must never reach a log line. The subscriber fields
    are nullable because they only matter when the subscriber is somebody
    other than the client (``subscriber_relationship != 'self'``); the
    claim scrub, not this table, decides when they are required.

    ``last_271`` and ``verified_at`` are written by the eligibility check,
    never by the chart form; they stay NULL until one has run.
    """

    __tablename__ = "patient_coverage"
    __table_args__ = (
        CheckConstraint(
            f"subscriber_relationship IN ({_sql_in_list(SUBSCRIBER_RELATIONSHIPS)})",
            name="ck_patient_coverage_subscriber_relationship",
        ),
        CheckConstraint(
            "subscriber_sex IS NULL OR subscriber_sex IN ('M', 'F', 'U')",
            name="ck_patient_coverage_subscriber_sex",
        ),
        Index(
            "ux_patient_coverage_active_primary",
            "patient_id",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    # Native uuid so the schema's ``has_patient_access`` policy applies.
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    payer_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("payers.id", ondelete="RESTRICT"), nullable=False
    )
    member_id: Mapped[str] = mapped_column(String(80), nullable=False)
    group_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    subscriber_relationship: Mapped[str] = mapped_column(String(10), nullable=False, default="self")
    subscriber_first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscriber_last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscriber_date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    # X12 DMG03 code set, same as ``patients.sex``.
    subscriber_sex: Mapped[str | None] = mapped_column(String(1), nullable=True)
    subscriber_address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscriber_address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscriber_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subscriber_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    subscriber_postal_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    plan_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_271: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


#: The X12 transactions a practice enrolls for with a payer. Remittance
#: (835) always needs one; claims (837P) and eligibility (270) only when the
#: payer's directory entry says so.
ENROLLMENT_TRANSACTION_TYPES: tuple[str, ...] = ("837P", "270", "835")

#: Where one enrollment request stands, in the clearinghouse's own
#: vocabulary lower-cased. ``provider_action_required`` is the one a person
#: has to act on; ``live`` is the one a claim or remittance needs.
PAYER_ENROLLMENT_REQUEST_STATUSES: tuple[str, ...] = (
    "draft",
    "stedi_action_required",
    "provider_action_required",
    "provisioning",
    "live",
    "rejected",
    "canceled",
)


class PayerEnrollmentRow(Base):
    """One enrollment request with a payer for one transaction type.

    Keyed by ``(payer_id, transaction_type)``: a practice files at most one
    request per payer per transaction, and the clearinghouse's own id for it
    is ``vendor_request_id``. Practice-level like ``payers`` — the practice
    is enrolled, not a clinician — so there is no ``user_id`` / ``patient_id``
    and no ``id`` either, which keeps the table out of ``enable_rls_on_schema``
    altogether; its isolation boundary is the tenant schema.

    ``requested_by_user_id`` is who asked for the enrollment and therefore
    who the reminder is addressed to when the payer wants something. It is
    deliberately not named ``user_id``: that name would make the row
    clinician-owned and hide the practice's enrollment from everyone else.

    ``instructions`` is the clearinghouse's wording of what the payer needs,
    kept to show on the payer row and in the reminder. It is stored and
    rendered, never logged.
    """

    __tablename__ = "payer_enrollments"
    __table_args__ = (
        CheckConstraint(
            f"transaction_type IN ({_sql_in_list(ENROLLMENT_TRANSACTION_TYPES)})",
            name="ck_payer_enrollments_transaction_type",
        ),
        CheckConstraint(
            f"status IN ({_sql_in_list(PAYER_ENROLLMENT_REQUEST_STATUSES)})",
            name="ck_payer_enrollments_status",
        ),
        Index("ix_payer_enrollments_vendor_request_id", "vendor_request_id"),
    )

    payer_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("payers.id", ondelete="CASCADE"), primary_key=True
    )
    transaction_type: Mapped[str] = mapped_column(String(4), primary_key=True)
    vendor_request_id: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by_user_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


#: Where a claim stands. It only ever moves forward on a receipt from the
#: next hop — a scrub with no blocking findings, a clearinghouse
#: acknowledgement, a payer acknowledgement, a remittance. ``rejected`` and
#: ``stalled`` are the two side exits: the first is a clearinghouse or payer
#: refusal that a corrected claim answers, the second is a watchdog noticing
#: no receipt arrived in time.
CLAIM_STATES: tuple[str, ...] = (
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
)

#: CLM05-3: ``1`` an original claim, ``7`` a replacement of a prior claim,
#: ``8`` a void of a prior claim. A replacement or a void always names the
#: claim it replaces in ``parent_claim_id``.
CLAIM_FREQUENCY_CODES: tuple[str, ...] = ("1", "7", "8")

#: CLM01 is at most 20 characters on the wire; the clearinghouse this
#: codebase files through caps it at 17.
CLAIM_CONTROL_NUMBER_MAX_LENGTH = 17


class ClaimRow(Base):
    """One professional claim, built from a session and filed with a payer.

    A snapshot, not a view: the billing identity, the subscriber and the
    diagnosis list are copied in when the claim is built, so an edit to the
    appointment or the coverage afterwards does not change what was (or
    will be) filed. A claim past ``draft`` is never edited in place; the
    correction is a new row with ``frequency_code`` ``7`` (or ``8`` to
    void) that points back here through ``parent_claim_id``.

    Carries ``patient_id`` and no ``user_id``, so ``enable_rls_on_schema``
    attaches the standard ``has_patient_access`` policy — the same posture
    as ``patient_coverage``. The snapshots hold the subscriber's name, date
    of birth and address and the diagnosis codes: protected health
    information that is stored here and never written to a log line.

    ``control_number`` is CLM01, the practice's own identifier for the
    claim on the wire; the clearinghouse and the payer echo it back on
    every acknowledgement, which is how those are matched to this row.
    Generated here, unique within the practice, never reused.
    """

    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(f"state IN ({_sql_in_list(CLAIM_STATES)})", name="ck_claims_state"),
        CheckConstraint(
            f"frequency_code IN ({_sql_in_list(CLAIM_FREQUENCY_CODES)})",
            name="ck_claims_frequency_code",
        ),
        CheckConstraint("total_charge_cents >= 0", name="ck_claims_total_charge_cents"),
        CheckConstraint("total_paid_cents >= 0", name="ck_claims_total_paid_cents"),
        UniqueConstraint("control_number", name="ux_claims_control_number"),
        Index("ix_claims_patient_id", "patient_id"),
        Index("ix_claims_state", "state"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    control_number: Mapped[str] = mapped_column(
        String(CLAIM_CONTROL_NUMBER_MAX_LENGTH), nullable=False
    )
    # Native uuid so the schema's ``has_patient_access`` policy applies.
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    coverage_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patient_coverage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payer_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("payers.id", ondelete="RESTRICT"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    frequency_code: Mapped[str] = mapped_column(String(1), nullable=False, default="1")
    parent_claim_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("claims.id", ondelete="RESTRICT"), nullable=True
    )
    total_charge_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    total_paid_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Ordered; the first code is the principal diagnosis. Stored as a JSON
    # list like the ``appointments.diagnosis_codes`` it is copied from.
    diagnosis_codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    place_of_service: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # The billing provider and the rendering provider as they stood when
    # the claim was built. Never carries the tax id itself — only its type
    # and last four, the same as the settings page shows.
    billing_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # The subscriber, the patient and the plan as they stood when the claim
    # was built.
    subscriber_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payer_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    adjudicated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The clearinghouse's own id for the filing (its ``correlationId``),
    # set by the synchronous accept; and the payer's claim number from its
    # 277CA (``tradingPartnerClaimNumber``), which a corrected or void
    # claim must quote back to the payer.
    vendor_claim_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    payer_claim_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # The outbox's pending marker. The key is minted and written, and the
    # timestamp set, BEFORE the submission call is made, so a crash between
    # the call and the commit leaves a claim the next run can reconcile
    # with the same key instead of filing a second one. Both are cleared
    # once the clearinghouse has answered.
    submission_idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    submission_pending_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # What the clearinghouse or the payer found wrong, as a list of
    # ``{source, code, description, followup_action}``. Set on a rejection.
    submission_findings: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    # When the last acknowledgement for this claim arrived, and when the
    # status poll last asked about it. The watchdog reads the first; the
    # poll backstop throttles on the second.
    last_receipt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


#: Every hop a claim takes and every alert raised for it, as the receipt
#: ledger records them. ``acknowledged`` is a 277CA that confirmed where the
#: claim already was; ``status_checked`` is a manual status check that found
#: nothing new. The two deadline kinds are the watchdog's ladder rungs.
CLAIM_EVENT_KINDS: tuple[str, ...] = (
    "submitted",
    "ch_accepted",
    "payer_accepted",
    "rejected",
    "stalled",
    "acknowledged",
    "status_checked",
    "deadline_approaching",
    "deadline_missed",
)


class ClaimEventRow(Base):
    """One receipt on a claim: a hop it took, or an alert raised about it.

    The claims tracker shows every hop with the moment its receipt arrived,
    and this is where those moments live. It is also what makes the
    acknowledgement paths idempotent: a clearinghouse webhook delivery is
    keyed by ``vendor_event_id`` (unique, so a redelivery cannot move the
    claim twice) and a deadline alert by ``(claim, kind, deadline kind,
    rung)`` (unique, so a restarted watchdog cannot re-raise it).

    Carries ``patient_id`` beside ``claim_id`` for the same reason
    ``claim_lines`` does: the row is isolated by the ``has_patient_access``
    policy of its claim without the policy engine learning a join.

    ``detail`` holds codes and vendor identifiers only — the clearinghouse
    edit codes or 277CA status codes behind a rejection, the correlation
    and trace ids behind a filing. Never a member id, a diagnosis or a
    name.
    """

    __tablename__ = "claim_events"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ({_sql_in_list(CLAIM_EVENT_KINDS)})", name="ck_claim_events_kind"
        ),
        UniqueConstraint("vendor_event_id", name="ux_claim_events_vendor_event_id"),
        # ``rung`` is NULL on everything but a deadline alert, and NULLs are
        # distinct under a unique constraint, so hops are never deduped.
        UniqueConstraint(
            "claim_id", "kind", "deadline_kind", "rung", name="ux_claim_events_deadline_rung"
        ),
        Index("ix_claim_events_claim_id", "claim_id"),
        Index("ix_claim_events_patient_id", "patient_id"),
        Index("ix_claim_events_vendor_transaction_id", "vendor_transaction_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(16), nullable=True)
    deadline_kind: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Days before the deadline the alert fired at (14, 7, 2), or 0 once it
    # has passed.
    rung: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vendor_event_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    vendor_transaction_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClaimLineRow(Base):
    """One service line on a claim: a CPT code on a date for an amount.

    ``appointment_id`` is a soft reference on purpose — no foreign key.
    Appointments get deleted; a money record does not, and it must keep
    reading correctly after the visit it came from is gone.

    Carries ``patient_id`` alongside ``claim_id`` so the row is isolated by
    the same ``has_patient_access`` policy as its claim, rather than by a
    join the policy engine would have to be taught. It is a copy of the
    claim's ``patient_id`` and is never different from it.

    ``allowed_cents`` / ``paid_cents`` / ``patient_resp_cents`` and
    ``adjustments`` (CARC/RARC entries) are written by remittance posting;
    they stay unset until an 835 arrives.
    """

    __tablename__ = "claim_lines"
    __table_args__ = (
        CheckConstraint("line_number > 0", name="ck_claim_lines_line_number"),
        CheckConstraint("units > 0", name="ck_claim_lines_units"),
        CheckConstraint("charge_cents >= 0", name="ck_claim_lines_charge_cents"),
        UniqueConstraint("claim_id", "line_number", name="ux_claim_lines_claim_line_number"),
        Index("ix_claim_lines_claim_id", "claim_id"),
        Index("ix_claim_lines_patient_id", "patient_id"),
        Index("ix_claim_lines_appointment_id", "appointment_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    appointment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # REF*6R — the line's own control number, echoed back per line on
    # acknowledgements. Derived from the claim's control number.
    line_control_number: Mapped[str] = mapped_column(String(30), nullable=False)
    service_date: Mapped[date] = mapped_column(Date, nullable=False)
    cpt: Mapped[str] = mapped_column(String(10), nullable=False)
    modifiers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    charge_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # 1-based positions into the claim's ``diagnosis_codes``.
    dx_pointers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Whether the visit was held over video. Copied from the appointment so
    # the scrub can check the place of service against it without reaching
    # back to a row that may have changed or gone.
    telehealth: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paid_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    patient_resp_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adjustments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
