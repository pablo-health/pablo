# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQLAlchemy ORM models for the practice schema.

Each practice gets its own PostgreSQL schema containing these tables.
Models map 1:1 to the existing domain dataclasses but are database-aware.

Complex nested structures (SOAP notes, transcripts, EHR route steps) are
stored as JSONB — they're always read/written as a whole and rarely queried.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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


class PatientRow(Base):
    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    patient_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    session_id: Mapped[str | None] = mapped_column(String(128), index=True)
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

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    patient_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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
    recurring_appointment_id: Mapped[str | None] = mapped_column(String(128), index=True)
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
    session_id: Mapped[str | None] = mapped_column(String(128))
    # Reminders
    reminder_24h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_1h_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AvailabilityRuleRow(Base):
    __tablename__ = "availability_rules"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
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
    patient_id: Mapped[str] = mapped_column(String(128), nullable=False)
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


class AuditLogRow(Base):
    """HIPAA audit log entry.

    Schema is intentionally PHI-free: IDs only, no denormalized names or
    emails. The `changes` JSONB stores field-name diffs (not values) and
    non-PHI structured data like counts and enum transitions. Routine
    log-review jobs can query this table directly without a sanitizing view.
    """

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False)
    patient_id: Mapped[str | None] = mapped_column(String(128), index=True)
    session_id: Mapped[str | None] = mapped_column(String(128))
    ip_address: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(Text)
    changes: Mapped[dict | None] = mapped_column(JSONB)
