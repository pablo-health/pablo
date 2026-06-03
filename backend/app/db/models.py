# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQLAlchemy ORM models for the practice schema.

Each practice gets its own PostgreSQL schema containing these tables.
Models map 1:1 to the existing domain dataclasses but are database-aware.

Complex nested structures (SOAP notes, transcripts, EHR route steps) are
stored as JSONB — they're always read/written as a whole and rarely queried.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..models.enums import ClinicianRole, OutcomeMeasureSource


class Base(DeclarativeBase):
    """Base class for all practice-schema ORM models."""


class ClinicianProfileRow(Base):
    __tablename__ = "clinician_profiles"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    practice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str | None] = mapped_column(String(50))
    credentials: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="clinician")
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    license_number: Mapped[str | None] = mapped_column(String(100))
    license_state: Mapped[str | None] = mapped_column(String(2))


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
    date_of_birth: Mapped[str | None] = mapped_column(String(10))
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


class TherapySessionRow(Base):
    __tablename__ = "therapy_sessions"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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
    # Export tracking — mirrors TherapySessionRow.export_*
    export_status: Mapped[str] = mapped_column(String(20), default="not_queued")
    export_queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    export_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    export_reviewed_by: Mapped[str | None] = mapped_column(String(128))
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
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True, index=True)
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
    granted_by: Mapped[str] = mapped_column(String(128), nullable=False)
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
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
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
    ``meets_criteria``, and the clinician-confirmed ICD-10-CM code. Distinct
    from ``outcome_measures`` (continuous symptom scores) — this is a
    point-in-time categorical determination.

    Per-tenant (lives in each ``practice_{id}`` schema), access governed by the
    app-layer ``has_patient_access`` function, same as ``notes`` /
    ``outcome_measures`` — no separate RLS policy.

    ``criterion_citations`` and ``confirmed_at`` are unused at launch; they are
    the seam for AI-assisted drafting (an ``inferred`` draft a clinician later
    confirms), shipped now so that capability needs no migration.
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
    meets_criteria: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Clinician-confirmed ICD-10-CM code, validated against the platform
    # icd10_codes catalog at write time. Null until confirmed.
    determined_icd10: Mapped[str | None] = mapped_column(String(10))
    diagnosis_label: Mapped[str | None] = mapped_column(String(120))
    # Per-criterion provenance for AI-assisted drafting (unused at launch).
    criterion_citations: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    # Null = unconfirmed draft (AI); set when a clinician confirms.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
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
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    session_type: Mapped[str] = mapped_column(String(30), nullable=False)
    video_link: Mapped[str | None] = mapped_column(Text)
    video_platform: Mapped[str | None] = mapped_column(String(30))
    notes: Mapped[str | None] = mapped_column(Text)
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
    # Reminders
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_1h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AvailabilityRuleRow(Base):
    __tablename__ = "availability_rules"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    enforcement: Mapped[str] = mapped_column(String(10), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GoogleCalendarTokenRow(Base):
    __tablename__ = "google_calendar_tokens"

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    encrypted_tokens: Mapped[str] = mapped_column(Text, nullable=False)
    calendar_id: Mapped[str | None] = mapped_column(String(255))
    sync_token: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    consecutive_error_count: Mapped[int] = mapped_column(default=0)


class ICalClientMappingRow(Base):
    __tablename__ = "ical_client_mappings"

    doc_id: Mapped[str] = mapped_column(String(500), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    ehr_system: Mapped[str] = mapped_column(String(50), nullable=False)
    client_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ICalSyncConfigRow(Base):
    __tablename__ = "ical_sync_configs"

    doc_id: Mapped[str] = mapped_column(String(300), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    due_date: Mapped[str | None] = mapped_column(String(10))
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
    uploaded_by_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)


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
    owner_user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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

    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
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
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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

    __table_args__ = (
        Index("ix_patient_documents_patient_deleted", "patient_id", "deleted_at"),
        CheckConstraint(
            "category IN ('chart', 'therapist_private', 'psychotherapy_notes')",
            name="ck_patient_documents_category",
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
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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
