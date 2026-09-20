# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""SQLAlchemy ORM models for the practice schema.

Each practice gets its own PostgreSQL schema containing these tables.
Models map 1:1 to the existing domain dataclasses but are database-aware.

Complex nested structures (SOAP notes, transcripts, EHR route steps) are
stored as JSONB — they're always read/written as a whole and rarely queried.

NO ``relationship()`` ANYWHERE, AND ONE OBLIGATION THAT FOLLOWS. There are 36
foreign keys in this file and no ORM relationships, which is deliberate: a
relationship lazy-loads when you touch the attribute, and that SELECT can fire
after the session's ``search_path`` or ``app.current_user_id`` has moved on, or
from a serializer walking the graph past the repository layer that owns tenant
scoping. Under FORCE ROW LEVEL SECURITY it then returns ZERO ROWS rather than
raising — so ``claim.lines`` would read as "this claim has no lines" instead of
"you asked outside the tenant context". Fail-closed is right for a guard and
catastrophic for an accessor.

The obligation: because the unit of work cannot see a dependency it was never
told about, **flush a parent before inserting a child that references it.**
Adding both in one ``session.add`` pair and flushing once inserts them in
mapper order, and the foreign key refuses the child. That failure is loud and
immediate, which is the trade — a wrong ordering raises, where a lazy load
would have quietly returned the wrong answer.
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
    ForeignKeyConstraint,
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

    Access is governed by :class:`PatientClinicianRow` grants, not by a
    ``user_id`` column on the row — that column was dropped in migration
    ``9dea1edf7fe0`` once the access table became the source of truth. The RLS
    policy here is ``has_patient_access(id, current_user)``.
    """

    __tablename__ = "patients"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    first_name: Mapped[str] = mapped_column(String(255), nullable=False)
    last_name: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name_lower: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    last_name_lower: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # What this person is called, as distinct from the legal name above that a
    # claim and a release of information need. The one name field the patient
    # owns outright — the portal profile screen writes it, and NULL or blank
    # means "use the first name" rather than "no name".
    preferred_name: Mapped[str | None] = mapped_column(String(255))
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
    # Chart closure (THERAPY-hek). Orthogonal to soft-delete: a closed chart is
    # a live, retained record whose care episode has ended. Closure is a
    # timestamp rather than a new ``status`` value, so existing list filters
    # keep returning closed charts. The hard-purge cron keys off
    # ``deleted_at``, never off this.
    chart_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    chart_closure_reason: Mapped[str | None] = mapped_column(Text)
    # Whether email is a consented PHI channel for this patient — anything
    # clinical in an email is a disclosure over an external channel.
    # Three states: NULL never asked, True consented, False declined. The
    # current decision lives here; the grant/withdrawal history is in the
    # audit trail, since recording a change is an audited event.
    phi_email_consent: Mapped[bool | None] = mapped_column(Boolean)
    phi_email_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A signed consent document backing the attestation. NULL when consent was
    # recorded as a clinician attestation with nothing attached.
    phi_email_consent_doc: Mapped[str | None] = mapped_column(Text)
    phi_email_consent_by: Mapped[str | None] = mapped_column(String(128))
    # Per-patient rate override in cents. NULL falls through to the appointment
    # type's default (app.scheduling_engine.services.rate_resolver). A real
    # column because sliding-scale arrangements are per-person, not a note
    # someone has to remember to read.
    rate_cents: Mapped[int | None] = mapped_column(Integer)
    # The arrangement in the clinician's own words. Never parsed — it exists so
    # the REASON for a rate survives staff turnover.
    sliding_scale_note: Mapped[str | None] = mapped_column(Text)
    # Flags a row for human merge review. NULL = created by staff in the normal
    # chart flow (most rows, and not suspicious). Non-NULL means an
    # unauthenticated intake surface that cannot verify the caller's claimed
    # identity created it, so it may duplicate an existing chart — 'voice'
    # today. Nothing de-duplicates automatically off this.
    origin: Mapped[str | None] = mapped_column(String(20))
    # Mailing address, for the X12 837P subscriber/patient loop. Optional — a
    # chart with no billing intent has no reason to require it.
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

    One row per administration — many rows per instrument over time. The index
    on ``(patient_id, instrument, administered_at)`` is the hot path for
    score-over-time charts in the chart view.

    Access is governed by the same ``has_patient_access`` checks the notes table
    uses — no separate RLS policy. See PABLO-o5k.
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


class PatientIntakeSubmissionRow(Base):
    """A patient's raw, as-submitted intake form body.

    Storage only. A row is the form a patient filled in before their first
    appointment, captured as-is in ``payload``. It is not a scored result:
    PHQ-9/GAD-7 scoring writes ``OutcomeMeasureRow`` with
    ``source='patient_self_report'`` instead, so this table only ever holds
    the raw submission a score may later be derived from.

    Tenant scope is implicit in the schema location — every row in
    ``practice_<id>.patient_intake_submissions`` belongs to that practice, so
    there is no ``practice_id`` column. ``patient_id`` is a ``uuid`` rather
    than a soft string id so the per-tenant ``has_patient_access`` policy
    applies to these rows directly, the same as the other per-patient chart
    tables.

    ``submitted_at`` is the patient-supplied completion time; ``created_at``
    is the server clock when the row was recorded. A submission is immutable
    once recorded — no ``updated_at``, no soft-delete.
    """

    __tablename__ = "patient_intake_submissions"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # uuid so the per-tenant has_patient_access RLS policy applies directly.
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)

    # When the patient completed the form (patient-supplied, not backdated
    # past the server's own record of it).
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # The raw, as-submitted form body. Per-question columns are a later
    # concern; the whole answer set is stored as JSON.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntakePacketTemplateRow(Base):
    """A named intake form the practice builds, across all its versions.

    Practice-level, not per-patient: this is the practice's own paperwork,
    the same for everyone it is sent to. There is no ``user_id`` or
    ``patient_id`` to scope a row by, so — like ``payers`` and
    ``scheduling_policy`` — its isolation boundary is the tenant schema and
    RLS is deliberately off (``_CORE_NOT_ROW_SCOPED`` in :mod:`app.db`).

    ``created_by`` is NULL on the form every practice starts with, which is
    laid down when the schema is provisioned and so has no author. A NULL
    there reads as "this came with Pablo", which is exactly what it means.

    Archiving is ``archived_at``, not a delete. A version that a patient
    filled in has to stay readable for as long as their record does, and the
    published versions hang off this row.
    """

    __tablename__ = "intake_packet_templates"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntakePacketVersionRow(Base):
    """One numbered version of an intake form.

    A version is a draft until ``published_at`` is set, and frozen after.
    That is what lets an answered form be read back against the exact
    questions it asked: editing a published version in place would rewrite
    the past, so the editor makes a new version from the old one instead.

    The freeze is enforced in the service layer rather than by a constraint,
    because what it forbids is a write to a *different* table — the item rows
    that point here. A CHECK cannot see across that join, and a trigger would
    put the rule somewhere no reader of this model would look for it.

    ``published_by`` is NULL while a version is a draft, and also on the
    version that ships with a fresh practice schema — nobody published that
    one either.
    """

    __tablename__ = "intake_packet_versions"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("intake_packet_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("template_id", "version", name="uq_intake_packet_versions_number"),
    )


class IntakeItemDefinitionRow(Base):
    """One question, or one piece of display text, on one version of a form.

    ``item_type`` is constrained to the vocabulary in
    :mod:`app.intake.items`, and ``config`` is that type's own settings —
    the choices a question offers, the ends of a scale, which measure to ask.
    JSONB because every type's settings are a different shape and none of
    them is ever queried; the type column is what the database constrains,
    and the discriminated union in ``app.intake.items`` is what constrains
    the blob.

    ``key`` is the practice's own name for the question ("substance_use"),
    unique within a version and assigned when the item is created. Rules
    point at it rather than at a position, so reordering a form cannot
    silently re-point one at a different question.

    ``position`` is the order the patient sees, unique per version so two
    items cannot claim the same place.

    ``resign_on_new_version`` marks an item whose answer does not carry
    forward — a consent that has to be given again when the form it is part
    of changes. Nothing reads it yet; it ships with the column it belongs to
    rather than costing a migration later.

    ``label`` is the question the patient reads, and ``help_text`` the line
    under it. A column rather than a member of ``config`` because every type
    has one and nothing about it varies by type: putting it in the blob would
    mean seventeen members carrying the same field and a renderer that has to
    know which one it is reading. Both are nullable — the questions the
    engine words itself (who you are, what brings you in, a published
    measure) have no wording for a practice to write, and a heading and a
    paragraph carry their text in ``config``. Publishing is where a question
    the practice wrote itself has to have one; see
    :func:`app.intake.items.validate_item_list`.
    """

    __tablename__ = "intake_item_definitions"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("intake_packet_versions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    item_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    help_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    resign_on_new_version: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint(
            "item_type IN ("
            "'section','instructions','demographics','reason','free_text',"
            "'single_choice','multi_choice','yes_no','scale','number','date',"
            "'instrument','emergency_contact','guardian','consent_document',"
            "'insurance_card','document_request')",
            name="ck_intake_item_definitions_type",
        ),
        UniqueConstraint("version_id", "position", name="uq_intake_item_definitions_position"),
        UniqueConstraint("version_id", "key", name="uq_intake_item_definitions_key"),
    )


class IntakeDocumentRow(Base):
    """One version of one document the practice asks people to read and sign.

    Practice-level like the three tables above, and registered
    not-row-scoped for the same reason: a consent document is the
    practice's own paperwork, identical whoever it is sent to, so there is
    no ``user_id`` or ``patient_id`` to key a row policy on. What somebody
    SIGNED is a different table, per-patient and row-scoped.

    **A row is a version, not a document.** ``document_key`` is the
    document; ``version`` numbers its revisions; the primary key identifies
    one revision of one document. Publishing a change never edits a row —
    it writes a new one with the same key and the next number — because a
    signature points at a version, and rewriting the text under it would
    change what somebody agreed to while leaving nothing in the record
    looking wrong.

    ``digest`` is the sha256 of the version's canonical text (see
    :mod:`app.intake.documents`), maintained on every write so a draft and
    a published version are read the same way. It is the evidence a
    signature carries, which is why it is taken over the words rather than
    over any rendering of them.

    Two rules live in indexes. ``uq_intake_documents_version`` says a
    document has one row per number. ``uq_intake_documents_draft`` is
    partial on ``published_at IS NULL`` and says a document has at most one
    unpublished draft — the same "there is only ever one thing being
    edited" rule the packet versions follow, enforced here by the database
    because the practice can start a new version from more than one screen.

    ``signer_roles`` is who has to sign: the patient, and a guardian where
    the practice asks for one. A list rather than a pair of booleans so the
    stored value reads the way the setting does.
    """

    __tablename__ = "intake_documents"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    document_key: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    requires_signature: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    signer_roles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("document_key", "version", name="uq_intake_documents_version"),
        Index(
            "uq_intake_documents_draft",
            "document_key",
            unique=True,
            postgresql_where=text("published_at IS NULL"),
        ),
    )


class PatientIntakeAssignmentRow(Base):
    """One patient being asked to fill in one version of one form.

    This is where the practice's paperwork meets a person. The three tables
    above are the same for everybody; this row says who was asked, which
    frozen version they were asked, and how far they have got.

    ``version_id`` points at a version rather than a template because the
    questions have to stay reconstructible: a published version never
    changes, so the answers underneath keep meaning what they meant. The
    service refuses to assign an unpublished one.

    ``status`` moves forwards only. ``assigned`` until the patient touches
    it, ``in_progress`` from their first saved answer, then either
    ``submitted`` (the patient is finished) or ``withdrawn`` (the practice
    withdrew the request). ``needs_correction`` and ``accepted`` belong to
    the review cycle and nothing writes them yet; the CHECK admits them now
    so the states that follow cost no migration. Each state has its own
    timestamp column rather than one transition log, because the questions
    asked of this table are "when was it sent" and "when did it arrive".

    **The partial unique index is the rule that a request is not duplicated.**
    One live assignment per patient per version: a clinician who clicks send
    twice, or reissues portal access after a link expired, must not leave the
    patient with two of the same form. ``accepted`` and ``withdrawn`` are
    outside the index, so a form genuinely asked for a second time later is
    still possible.

    The unique constraint on ``(id, patient_id)`` is not redundant with the
    primary key: it is the target of the composite foreign key on
    :class:`PatientIntakeResponseRow`, which keeps that table's denormalized
    ``patient_id`` from ever disagreeing with the assignment's owner. Same
    pattern, and the same reason, as :class:`PatientMessageThreadRow`.

    ``receipt_code`` is what the patient is given when they hand the form
    in — a short, unambiguous code that unlocks nothing and exists so the
    two sides of a phone call can name the same submission. NULL until
    then, and unique within the practice, which is the scope that matters:
    the schema is the boundary, so a code only ever has to be findable
    here. See :mod:`app.intake.receipts`.

    ``legacy_submission_id`` is set only on a row adopted from the fixed
    intake form that shipped before a practice could build its own. Unique,
    so adopting twice is refused by the database rather than by the command
    remembering to check — which is what makes
    ``app.bin.adopt_intake_submissions`` safe to re-run. NULL on every row
    a patient filled in through the portal, which is all of them but the
    first batch.
    """

    __tablename__ = "patient_intake_assignments"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey(
            "intake_packet_versions.id",
            name="fk_patient_intake_assignments_version",
        ),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    assigned_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_code: Mapped[str | None] = mapped_column(String(16))
    legacy_submission_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('assigned','in_progress','submitted',"
            "'needs_correction','accepted','withdrawn')",
            name="ck_patient_intake_assignments_status",
        ),
        UniqueConstraint("id", "patient_id", name="uq_patient_intake_assignments_id_patient"),
        # Unique indexes rather than unique constraints, so the revision that
        # adds them to an existing practice can be written with ``CREATE
        # UNIQUE INDEX IF NOT EXISTS`` and stay idempotent under the fan-out.
        # A unique constraint has no such form, and the two are not the same
        # shape in the catalog — which is exactly what the fresh-versus-
        # migrated comparison in the integration suite would catch.
        Index("uq_patient_intake_assignments_receipt", "receipt_code", unique=True),
        Index(
            "uq_patient_intake_assignments_legacy_submission",
            "legacy_submission_id",
            unique=True,
        ),
        Index(
            "uq_patient_intake_assignments_active",
            "patient_id",
            "version_id",
            unique=True,
            postgresql_where=text("status NOT IN ('accepted','withdrawn')"),
        ),
    )


class PatientIntakeResponseRow(Base):
    """One answer to one question on one assignment.

    A row per item rather than one blob per assignment, because the patient
    fills a form in over several sittings and each save has to land on its
    own. ``value`` is JSONB for the same reason the item's ``config`` is:
    every kind of question is answered with a different shape, and none of
    them is ever queried by its contents.

    ``draft`` is the whole lifecycle. A patient's saved answers are drafts
    until they submit, and a draft is the only row in this model a patient
    may change — which is what makes "what was submitted" a stable record
    once it stops being one. ``superseded_by`` points at the row that
    replaced this one, so a corrected answer leaves the original readable
    rather than overwriting it. Nothing writes either transition yet; both
    ship with the columns they belong to rather than costing a migration.

    The partial unique index is what makes saving an answer an upsert: at
    most one live draft per question per assignment, so a patient who
    answers the same question twice updates rather than accumulates.

    ``patient_id`` is denormalized from the assignment, exactly as
    ``patient_messages`` denormalizes it from its thread and for the same
    reason: every per-patient policy keys on a ``patient_id`` column, so
    carrying it means this table needs no bespoke policy branch. The
    composite foreign key to ``(id, patient_id)`` is what keeps the copy
    honest — a response whose ``patient_id`` disagrees with its
    assignment's is rejected by the database, not by a convention.
    """

    __tablename__ = "patient_intake_responses"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey(
            "intake_item_definitions.id",
            name="fk_patient_intake_responses_item",
        ),
        nullable=False,
    )
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    draft: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["assignment_id", "patient_id"],
            [
                "patient_intake_assignments.id",
                "patient_intake_assignments.patient_id",
            ],
            ondelete="CASCADE",
            name="fk_patient_intake_responses_assignment",
        ),
        Index(
            "uq_patient_intake_responses_live_draft",
            "assignment_id",
            "item_id",
            unique=True,
            postgresql_where=text("superseded_by IS NULL AND draft"),
        ),
    )


class PatientIntakeSignatureRow(Base):
    """One person typing their name against one consent document.

    The evidence record for a signature, and the one table in this feature
    that exists to be read years after it was written. Everything on it is
    chosen so that "what did this person agree to, and how do we know it was
    them" can be answered from the row alone.

    **It records the version, not the document.** ``document_version_id``
    points at the exact revision that was on the screen, and
    ``document_digest`` is that revision's digest copied at signing. The
    copy is not redundant with the foreign key: the digest travels with the
    signature, so a signed record still names its text even when it is read
    through an export that never joined to ``intake_documents``. The route
    checks the two agree before it writes, so a copy that has drifted cannot
    be created.

    **The session is named by its handle, never by its token.**
    ``session_id`` is the server-side id of the portal session the request
    authenticated with, and ``auth_strength`` is how strongly that session
    had proved who was holding it — ``stepped_up`` on every row the route
    writes, because it refuses a single-factor caller. Storing the strength
    rather than inferring it later is what makes the row self-describing:
    the policy that required step-up can change, and this says what was
    actually true at the time.

    ``ip`` is a string rather than ``INET`` for the same reason
    ``audit_logs.ip_address`` is. The value comes from the same request
    extractor, which reads a proxy header; a column that refused a malformed
    one would turn somebody else's misconfigured proxy into a failure to
    record a signature, and the address is evidence about a request rather
    than something this table ever queries as a network address.

    ``evidence_digest`` is a sha256 over the fields above (see
    :mod:`app.intake.signatures`). It is a consistency check on the row, not
    a seal — anybody who can rewrite a column can rewrite it too. What it
    catches is the realistic failure: a migration, a backfill or a bug
    changing a column with nothing else looking wrong.

    **A guardian signature is the same session with a different role.** The
    portal has one principal, the patient's, and v1 does not mint a separate
    guardian identity — so a guardian signature is recorded as an
    attestation made from the patient's session, with the guardian's own
    typed name. ``signer_role`` is what tells the two apart, and the screen
    says plainly which one is being taken.

    The partial unique index is the rule that one person signs one document
    once per form. ``superseded_at`` is what takes a row out of it: a
    signature invalidated by a newer version of the document is superseded
    rather than deleted, because a signature that was taken is a fact
    whatever happened afterwards. Nothing writes it yet — the re-sign rule
    today refuses to take a stale signature at all, rather than taking one
    and retiring the old — so it ships with the column it belongs to rather
    than costing a migration later, exactly as ``superseded_by`` does on
    :class:`PatientIntakeResponseRow`.

    ``patient_id`` is denormalized from the assignment and held honest by
    the composite foreign key, the same pattern and the same reason as
    :class:`PatientIntakeResponseRow`.
    """

    __tablename__ = "patient_intake_signatures"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey(
            "intake_item_definitions.id",
            name="fk_patient_intake_signatures_item",
        ),
        nullable=False,
    )
    document_version_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey(
            "intake_documents.id",
            name="fk_patient_intake_signatures_document",
        ),
        nullable=False,
    )
    document_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    signer_role: Mapped[str] = mapped_column(String(16), nullable=False)
    signer_typed_name: Mapped[str] = mapped_column(String(160), nullable=False)
    consent_statement_version: Mapped[str] = mapped_column(String(16), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    auth_strength: Mapped[str] = mapped_column(String(16), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "signer_role IN ('patient','guardian')",
            name="ck_patient_intake_signatures_role",
        ),
        ForeignKeyConstraint(
            ["assignment_id", "patient_id"],
            [
                "patient_intake_assignments.id",
                "patient_intake_assignments.patient_id",
            ],
            ondelete="CASCADE",
            name="fk_patient_intake_signatures_assignment",
        ),
        Index(
            "uq_patient_intake_signatures_live",
            "assignment_id",
            "item_id",
            "signer_role",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )


class PatientIntakeArtifactRow(Base):
    """One file a patient attached to a question that asked for one.

    A photograph of an insurance card, the referral letter a practice asked
    them to bring. The file itself is a :class:`PatientDocumentRow` in the
    ``intake_artifact`` category — this row is the link between it and the
    question, which is what lets the form ask "has the back of the card
    arrived" without the document table knowing anything about forms.

    Three columns carry the whole rule.

    ``document_id`` is unique, so one file answers one question: attaching
    the same photograph as both the front and the back of a card is refused
    by the database rather than by whichever caller remembered to look.

    ``side`` is set on a card and NULL on anything else, and the partial
    unique index on ``(assignment_id, item_id, side)`` makes "one front, one
    back" a fact about the table. It is partial because a plain unique index
    would also mean one file per document-request question, and a practice
    asking for prior records may well be sent three.

    ``patient_id`` is denormalized from the assignment and held honest by
    the composite foreign key, the same pattern and the same reason as
    :class:`PatientIntakeResponseRow` — it is what lets this table be
    policied by a plain column comparison rather than a join.

    There is no soft delete. A file removed before the form is handed in was
    never sent: the row goes, and the document it points at is tombstoned
    with it. After the form is in, nothing here is removable at all — that
    is the route's rule, because "what was submitted" has to stay a stable
    record.
    """

    __tablename__ = "patient_intake_artifacts"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    assignment_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    item_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("intake_item_definitions.id", name="fk_patient_intake_artifacts_item"),
        nullable=False,
    )
    document_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey(
            "patient_documents.id",
            ondelete="CASCADE",
            name="fk_patient_intake_artifacts_document",
        ),
        nullable=False,
        unique=True,
    )
    side: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "side IS NULL OR side IN ('front','back')",
            name="ck_patient_intake_artifacts_side",
        ),
        ForeignKeyConstraint(
            ["assignment_id", "patient_id"],
            [
                "patient_intake_assignments.id",
                "patient_intake_assignments.patient_id",
            ],
            ondelete="CASCADE",
            name="fk_patient_intake_artifacts_assignment",
        ),
        Index(
            "uq_patient_intake_artifacts_side",
            "assignment_id",
            "item_id",
            "side",
            unique=True,
            postgresql_where=text("side IS NOT NULL"),
        ),
        Index(
            "ix_patient_intake_artifacts_assignment_item",
            "assignment_id",
            "item_id",
        ),
    )


class IntakeBlankFormRow(Base):
    """One of the practice's own empty forms, for a patient to print.

    The fallback for a practice that still works from paper: a
    ``document_request`` item can name one of these, and the question then
    offers it for download before asking for the filled-in copy back.

    **On nobody's chart, and that is why it is here rather than on
    ``patient_documents``.** A blank form holds no patient's information —
    it is the practice's stationery — so filing it against a chart would
    mean either an arbitrary patient or a nullable ``patient_id`` on a table
    whose every row policy keys on that column. It is practice-level
    instead, registered not-row-scoped like ``compliance_items``: its
    isolation boundary is the tenant schema, which is the same boundary the
    form it belongs to already lives inside.

    Uploaded by a clinician through the same two-phase signed-URL flow every
    other file in the system uses; ``finalized_at`` is NULL between the two
    halves, so an upload that was started and abandoned never appears
    anywhere. ``deleted_at`` tombstones a form a practice has stopped using,
    which leaves an item that still names it showing no download rather than
    a broken one.
    """

    __tablename__ = "intake_blank_forms"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    gcs_path: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    uploaded_by: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_intake_blank_forms_deleted", "deleted_at"),)


class PatientMessageThreadRow(Base):
    """One secure-message conversation between a patient and their practice.

    The thread is the envelope; :class:`PatientMessageRow` holds the words.
    Both live in the practice schema, so there is no ``practice_id`` column —
    tenant scope is the schema location, as everywhere else here.

    ``subject`` is typed by the patient and may be absent; a thread with no
    subject is an ordinary thread, not a defective one. ``status`` is the
    lifecycle: a practice closes a resolved thread, and a clinician writing
    back into a closed one reopens it in the same statement, so ``closed_at``
    and ``status`` cannot disagree.

    ``closed_by`` and ``assigned_user_id`` hold clinician user ids and are
    plain columns rather than foreign keys, matching every other user id in
    this schema — the users live in ``platform``.

    **``assigned_user_id`` routes, it does not gate.** Any clinician holding
    a ``patient_clinicians`` grant reads and answers this thread whether it
    is assigned to them, to somebody else, or to nobody; the column exists so
    a group practice can divide the work, and the row policies never mention
    it. A reader looking for the access rule should look at
    ``has_patient_access`` and stop.

    ``clinician_last_read_at`` is one timestamp for the practice side, not
    one per clinician: what a thread list needs is "has anything arrived
    since somebody here looked", and per-clinician state would be a second
    table to answer a question nobody asked. The patient side keeps its own
    per-message ``read_at`` on :class:`PatientMessageRow`, which is a
    different fact and stays separate.

    ``last_message_at`` is denormalized from the newest message so a patient's
    thread list sorts without touching the message table.

    The unique constraint on ``(id, patient_id)`` looks redundant next to the
    primary key and is not: it is the target of the composite foreign key on
    :class:`PatientMessageRow`, which is what stops a message's denormalized
    ``patient_id`` from ever disagreeing with its thread's owner.

    Deleting a patient takes their correspondence with it, the same as their
    notes: ``patient_id`` cascades from ``patients``, and the messages
    cascade from the thread. Retention is the chart's retention — there is no
    separate lifetime for what a patient wrote here.
    """

    __tablename__ = "patient_message_threads"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    assigned_user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
    clinician_last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','closed')",
            name="ck_patient_message_threads_status",
        ),
        UniqueConstraint("id", "patient_id", name="uq_patient_message_threads_id_patient"),
    )


class PatientMessageRow(Base):
    """One message in a :class:`PatientMessageThreadRow`.

    ``patient_id`` is denormalized from the thread on purpose. Every
    per-patient policy in :func:`app.db.enable_rls_on_schema` — the clinician
    ``has_patient_access`` arm and the patient-principal arm alike — keys on a
    ``patient_id`` column, so carrying it means this table needs no bespoke
    policy branch at all. ``chat_messages`` is the counter-example: it has no
    owning column, so it has a hand-written parent-join predicate in two
    places. The composite foreign key to ``(id, patient_id)`` is what keeps
    the denormalized copy honest — a message whose ``patient_id`` disagrees
    with its thread's is rejected by the database, not by a convention.

    ``body`` is plain ``Text``. "Encrypted at rest" is the storage layer's
    job; there is no application-layer envelope here, and adding one would
    put the practice's own clinicians outside their patients' messages.

    ``sender`` names who wrote the message, not which credential posted it:
    ``'practice'`` is for a message the practice itself sends without a
    clinician composing it. Nothing in the engine writes that value today.

    ``read_at`` is set when the *patient* reads a message somebody else sent
    them. It stays NULL on the patient's own messages.
    """

    __tablename__ = "patient_messages"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    thread_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    sender: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "sender IN ('patient','clinician','practice')",
            name="ck_patient_messages_sender",
        ),
        ForeignKeyConstraint(
            ["thread_id", "patient_id"],
            ["patient_message_threads.id", "patient_message_threads.patient_id"],
            ondelete="CASCADE",
            name="fk_patient_messages_thread",
        ),
        Index(
            "ix_patient_messages_thread_created",
            "thread_id",
            "created_at",
        ),
    )


class PortalInviteChallengeRow(Base):
    """One outstanding portal invitation, keyed by the invite token's ``jti``.

    The invite token is signed and self-expiring, but SINGLE-USE and
    attempt-limiting are not properties a stateless token can carry, and
    this row is both. Created when a clinician issues an invitation;
    ``consumed`` flips on the one successful redemption. Rows are kept after
    consumption rather than deleted, so replaying the same token is a
    *refusal* rather than an unknown-jti lookup miss — both answer 401 to
    the caller, but only one of them is legible in the record afterwards.

    ``otp_hash`` is an HMAC of the one-time code, peppered with the portal
    signing key. The code itself is never stored, so this table on its own
    yields nothing: a leaked magic link still cannot redeem.

    Tenant scope is the schema location, as everywhere else here, so there
    is no ``practice_id`` column. No column holds a name, a message or
    anything clinical — a hash, two timestamps and a counter.

    The table name predates the portal naming and is kept as it is: it is
    the identity of rows that already exist in deployed practices, and
    renaming it would orphan every invitation in flight.
    """

    __tablename__ = "companion_auth_challenges"
    __table_args__ = (
        # The clinician's revoke-all sweep, and the outstanding-invitation
        # state the access-state route reads, both go by patient.
        Index("ix_companion_auth_challenges_patient", "patient_id"),
    )

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    otp_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    consumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PortalSessionRow(Base):
    """One patient portal session, keyed by the session token's ``jti``.

    A session token is only as good as its row: the resolver refuses a token
    whose row is missing, revoked or past ``expires_at``, so a clinician's
    revoke takes effect immediately rather than waiting out the token's TTL.

    ``chain_started_at`` is what bounds sliding renewal. Refresh rotates the
    ``jti`` (new row, old row retired), which on its own would let a session
    live forever one hour at a time; carrying the ORIGINAL redemption's
    timestamp forward through every rotation gives the chain a hard ceiling,
    after which the patient redeems a fresh invitation.

    Same two notes as the table above: tenant scope is the schema location,
    and the name is kept so existing sessions stay the rows they are.
    """

    __tablename__ = "companion_sessions"
    __table_args__ = (
        # Revoke-all-for-this-patient is the clinician kill switch; it is
        # the only query here that is not a primary-key lookup.
        Index("ix_companion_sessions_patient", "patient_id"),
    )

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # When the CHAIN this session belongs to was first redeemed — carried
    # forward unchanged by every refresh. Bounds sliding renewal.
    chain_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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

    One row per assessment: per-criterion responses and gate attestations
    against a versioned definition (snapshotted by ``definition_code`` +
    ``definition_version``), the computed ``meets_criteria`` (NULL for
    ``checklist`` definitions, which make no algorithmic determination), and the
    clinician-confirmed ICD-10-CM code. Distinct from ``outcome_measures``
    (continuous symptom scores): this is a point-in-time categorical
    determination.

    Access is governed by ``has_patient_access``, like ``notes`` /
    ``outcome_measures`` — no separate RLS policy.

    ``criterion_citations`` and ``confirmed_at`` are unused at launch, reserved
    for provenance-tracked capture (which source supports each criterion, plus
    a confirmation step) and shipped early so that needs no migration.
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
    # --- Cancellation record ---------------------------------------------
    #
    # A practice's notice period is a FEE boundary, not a permission one:
    # anybody may cancel at any time, because the alternative to a late
    # cancellation is a no-show, which costs the slot AND the warning. These
    # columns make the fee defensible afterwards.
    #
    # ``updated_at`` cannot stand in for ``cancelled_at`` — any later edit moves
    # it, so by billing time it may say nothing about when the slot was given
    # up. And without ``cancelled_by``, a lapsed hold, a clinician rearranging
    # their week and a patient cancelling an hour ahead are the same row, only
    # one of which anyone may be charged for.
    #
    # All NULL on rows cancelled before this shipped, which reads as "not known"
    # rather than as a claim the cancellation was early or nobody's.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cancelled_by_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    # Frozen at the moment of cancelling rather than derived on read: the
    # policy is editable, so recomputing later would silently change whether a
    # past cancellation had been chargeable.
    late_cancellation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # The appointment that replaced this one when it was rescheduled. A move
    # leaves two rows — this one cancelled, and a new one at the new time — so
    # the slot given up survives as a record instead of being overwritten. Set
    # means "moved"; NULL on a cancelled row means "cancelled outright". No
    # foreign key, matching ``recurring_appointment_id``.
    superseded_by_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)
    # The caller's attestation that it was told the change was late and went
    # ahead. Load-bearing because the API REFUSES a late change without it, so
    # a client cannot reach this state without having been handed the warning
    # to show — which is what makes it evidence rather than a checkbox.
    late_change_acknowledged: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
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
    #: The appointment type this rule governs, or NULL for all of them.
    #:
    #: NULL is the practice-wide rule and what every pre-existing row means.
    #: Deleting the type CASCADES the rule away rather than nulling it: a cap
    #: of "two intakes a day" that quietly became "two appointments a day"
    #: because somebody tidied up a type would be a far worse surprise than
    #: the rule disappearing along with the thing it was about.
    appointment_type_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("appointment_types.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    #: Whether other types may be offered inside this rule's window.
    #:
    #: True everywhere until a practice deliberately says otherwise, so
    #: scoping a rule to a type changes nothing for any other type.
    allow_other_types: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AppointmentTypeRow(Base):
    """A kind of appointment: how long it runs, who it is for, when it may be offered.

    This started as a fee table and is now the unit of scheduling. A type
    carries its own length and booking window, because a fifteen-minute
    consultation and a sixty-minute intake want different notice, lead time and
    horizon. ``default_fee_cents`` is the fee absent a per-patient override —
    see :mod:`app.scheduling_engine.services.rate_resolver`.

    ``appointments.appointment_type_id`` references this table, so a rename no
    longer orphans the appointments booked under it. Two places differ, both
    deliberately:

    * ``appointments.session_type`` keeps the name the appointment was booked
      under, so history reads correctly after a rename.
    * ``booking_links.appointment_type_id`` holds this id as a plain value with
      no foreign key: that table is PLATFORM-scoped (a public slug resolves
      before any tenant schema is selected) and a platform table cannot key
      into one of N per-tenant schemas. Validated in the application instead.
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

    #: The service code this type bills as — a CPT, typed by the practice.
    #: Nullable because a practice that never bills insurance and never issues
    #: an estimate needs none, and never inferred from ``duration_minutes``:
    #: the psychotherapy codes band by session length, but the bands have
    #: edges and picking a billing code for a clinician is not ours to do.
    cpt: Mapped[str | None] = mapped_column(String(10))

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

    Answers what an appointment type does not: how late a patient may cancel,
    how a new enquiry starts, whether patients may book at all. A type says
    what an appointment IS; this says what the practice allows to happen to its
    calendar. Singleton, pinned by ``CHECK (id = 1)``, so a save upserts.

    Every gate defaults off or strict — ``self_book_existing`` and
    ``self_book_new`` false, ``self_book_mode`` ``request`` rather than
    ``auto`` — because a practice upgrading into this code must not discover
    that patients can suddenly book it.

    Whether a PARTICULAR type may be self-booked lives on
    ``appointment_types.self_bookable``. Two switches deliberately: this one is
    "self-booking is a thing I allow at all", that one is "and this type in
    particular". Both must be on.

    Storing policy is all this does; enforcing it at booking time is separate
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

    Singleton, pinned by ``CHECK (id = 1)`` — same shape as
    :class:`SchedulingPolicyRow` and for the same reason: practice-level
    configuration, so no ``user_id`` / ``patient_id`` to scope it by.

    ``tax_id_encrypted`` holds the EIN or SSN at rest, AES-256-GCM encrypted the
    same way OAuth calendar tokens are (``app.services.token_encryption``).
    ``tax_id_last4`` is kept separately in the clear purely for display, so a
    settings page can show "···· 1234" without decrypting anything.
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
    #: May a clinician write off a client's balance as a courtesy — a waiver
    #: with no financial-hardship or billing-error basis behind it. Off by
    #: default: a practice opts in before anyone can give money away this way.
    allow_courtesy_writeoffs: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: The balance, in cents, at or under which a ``small_balance`` write-off
    #: is allowed. Below the cost of chasing it, by the practice's own
    #: judgment — not a discount, a threshold for not bothering to collect.
    small_balance_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=500, server_default="500"
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_practice_billing_profile_singleton"),
        CheckConstraint(
            "tax_id_type IN ('ein', 'ssn')", name="ck_practice_billing_profile_tax_id_type"
        ),
        CheckConstraint(
            "small_balance_cents >= 0", name="ck_practice_billing_profile_small_balance"
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
    source_ref: Mapped[str | None] = mapped_column(String(128))
    """What an automatically-filed row belongs to, for the filer to find again.

    NULL on everything a person entered, which is almost every row: a licence
    renewal is not raised by anything and nothing needs to look it up.

    Set by :mod:`app.claims.events` on a payer-enrollment reminder, to the
    vendor's request id it arrives with (``ClaimEvent.control_number`` — the
    field names a claim for every other kind, and for this one there is no
    claim). That reminder used to be found by a prefix of ``notes``, which the
    compliance update route replaces wholesale, so a clinician tidying her own
    note severed the link and the next refresh filed a duplicate. A column the
    route does not write cannot be edited away.

    Unique per ``(user_id, item_type)`` where it is set — see
    ``ux_compliance_items_source_ref``, which is what makes the duplicate
    impossible rather than merely unlikely.
    """
    due_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        # One automatically-filed row per thing per clinician. Partial, because
        # ``source_ref`` is NULL on everything a person entered and several
        # licences with no source are not a conflict.
        Index(
            "ux_compliance_items_source_ref",
            "user_id",
            "item_type",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
    )


class ComplianceDocumentRow(Base):
    """Dormant data-model rail for the Phase 3 compliance vault.

    Will back uploaded artifacts (license PDFs, malpractice declarations, CAQH
    attestations, BAAs) attached to a ``ComplianceItemRow``. Shipping the table
    now — no routes, storage wiring or UI — means self-hosters need no forced
    migration when the vault surface lands. ``storage_uri`` is opaque (gs://
    today, s3:// or local fs self-hosted) so the backend can swap without a
    column change, and ``document_type`` is free-form while the feature shape
    settles.
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

    The regulatory relationships a clinician must keep current: physician
    delegation, NP collaborative agreements, PA supervision, pre-licensure
    clinical supervision. These describe the clinician's own professional
    standing and her named supervisor's, not any patient, so the table sits in
    the practice schema beside ``compliance_items`` and is gated by ``user_id``.

    The review deadline rides an existing ``compliance_items`` row
    (``compliance_item_id``) to reuse the reminder machinery, with
    ``next_review_date`` mirroring that item's ``due_date``. Nullable, so a
    relationship can be recorded before its review item exists.
    ``relationship_type`` and ``status`` are free-form (validated at the service
    layer) to stay flexible across professions and jurisdictions.
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

    Lives in the practice schema alongside ``patients``; no ``tenant_id``
    column, since schema-per-practice already isolates rows. ``patient_id`` and
    ``caller_system_prompt`` are immutable after insert, enforced by the service
    layer rather than a constraint — the audit guarantee is a service-level
    invariant, not a schema one.

    Removing a conversation cascades to its messages via the FK below. See
    chat-design §6.6 for user-initiated hard-delete semantics.
    """

    __tablename__ = "chat_conversations"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False, index=True)
    # NULL means the patient started this conversation themselves — there is
    # no clinician actor to record. Not a sentinel id, deliberately: a
    # sentinel reads as a real clinician to every query that does not know
    # better, whereas NULL forces the "no owner" case to be handled on
    # purpose. It is also half the patient row test, since "conversations
    # about me" and "conversations I started" are otherwise the same set —
    # see ``_patient_principal_predicate_for`` in ``app.db``.
    owner_user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
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
    """An uploaded patient document (THERAPY-ak6m.2).

    Two principals write here and exactly one column says which: ``user_id``
    for the clinician who uploaded it, ``uploaded_by_patient_id`` for the
    patient. ``ck_patient_documents_one_uploader`` makes that exclusive, so a
    row can neither claim both nor go unattributed — which is what lets the
    chart say who a document came from without inferring it.

    Per-tenant. The RLS shape combines two policies keyed on ``category``
    (:class:`app.models.DocumentCategory` carries the regulatory rationale):

    * ``chart`` rows follow :class:`NoteRow`'s patient-access model — anyone
      with a ``patient_clinicians`` grant sees them. Default, and it matches
      clinical reality: co-treating clinicians share the chart.
    * ``therapist_private`` and ``psychotherapy_notes`` collapse to uploader-
      only ``user_id`` ownership. The predicate is identical for both; they
      stay distinct so disclosure workflows (release-of-records, right-of-
      access) can filter on the HIPAA-meaningful boundary later.

    The patient principal has an additive arm of its own, narrowed to the
    categories :attr:`app.models.DocumentCategory.is_patient_facing` names —
    a patient reaching their own chart through the portal gets the documents
    that surface belongs to, not the clinical record behind it.

    See :func:`app.db.enable_rls_on_schema` for the policy body.

    Lifecycle: ``finalized_at`` is NULL between init (signed URL minted,
    placeholder row inserted) and finalize (GCS object verified, PyMuPDF
    extraction run), and list/get filter it out so abandoned inits never
    appear. ``extracted_text`` is NULL when PyMuPDF returned under 100 chars
    — treated as a scanned PDF, OCR'd by ak6m.2.3. ``deleted_at`` non-NULL is
    soft-deleted; the GCS cleanup cron is deferred to ak6m.2.1.
    """

    __tablename__ = "patient_documents"

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False),
        ForeignKey("patients.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Nullable since patients upload too: the uploader is one column or the
    # other, never both, and ck_patient_documents_one_uploader enforces it.
    user_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), index=True)
    # Set only on a patient's own upload, and always to the chart's patient —
    # the row policy pins it to the calling principal and no route reads it
    # from a request body. No foreign key: ``patient_id`` beside it already
    # carries the cascade, and a second one on the same patient would add a
    # delete path without adding a constraint that column does not have.
    uploaded_by_patient_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False))
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
            "category IN ('chart', 'consent', 'intake_artifact', 'message', "
            "'therapist_private', 'psychotherapy_notes')",
            name="ck_patient_documents_category",
        ),
        CheckConstraint(
            "(user_id IS NOT NULL) <> (uploaded_by_patient_id IS NOT NULL)",
            name="ck_patient_documents_one_uploader",
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
        # every request runs with one of the two identity GUCs empty. Every other
        # principal column is a uuid, where '' fails the cast and never matches;
        # this one is VARCHAR for the reasons above, so '' is storable and
        # '' = '' is true. Without this constraint the empty id is a bucket
        # shared by every principal whose other GUC is cleared — readable and
        # writable across the clinician/patient boundary, invisible to
        # legitimate readers, and unreachable by the retention purge.
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

    One row per prescribing visit, sibling of ``notes`` /
    ``diagnostic_assessments`` inside each ``practice_{id}`` schema. Access is
    enforced via ``has_patient_access`` on ``patient_id``, like the rest of the
    chart — no separate RLS policy.

    Prescriber credentials and the delegating physician are **snapshotted**
    here rather than only referenced, so the record reflects what was true at
    prescribing time even if the standing ``clinician_profiles`` /
    ``supervision_relationships`` rows later change. ``delegation_ref`` points
    at the delegation agreement in force.

    ``ruleset_version`` (e.g. ``"MI-RX-2026.06"``) stamps the ruleset in force
    so the rules applied can be reconstructed later. ``status`` /
    ``finalized_at`` back the layer-3 finalization gating, shipped early so
    that capability needs no migration.

    The enforcement evaluator assembles a flat context from these columns; the
    curated ruleset's ``trigger`` / ``satisfied_when`` paths resolve as:

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

    The unit the rules engine evaluates. ``schedule`` + ``drug_class`` select
    which ruleset items apply — a Schedule II stimulant triggers the delegation
    / dual-DEA / MAPS items, a non-controlled drug (``schedule "none"``)
    triggers nothing — and ``days_supply`` / ``refills`` drive the conditional
    triggers and ``satisfied_when`` checks. ``patient_id`` is denormalized from
    the encounter so patient-access checks and chart queries key on it directly,
    like ``notes`` / ``patient_medications``.
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

    Finalized encounters are immutable; the only lawful change is an addendum.
    Addenda are append-only — no ``updated_at`` / ``deleted_at`` — and form a
    tamper-evident hash chain: ``digest`` is this addendum's content digest and
    ``prev_digest`` the previous chain link (the encounter's
    ``integrity_digest`` for the first, the prior addendum's thereafter), so
    removing or reordering one breaks every digest after it. Patient-scoped via
    ``has_patient_access``, same as the encounter.

    ``label`` is the kind of correction, ``text`` the correction itself in the
    clinician's own words. ``created_at`` is the server clock at writing —
    backdating is not representable.
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

    The record behind "no checkbox without evidence". When
    ``app.rules.enforcement.evaluate_enforcement`` runs a curated ruleset
    against an open encounter + prescription, ``app.prescribing.attestation``
    persists one row per *applicable* item: its computed ``status``, its
    ``flag_behavior`` / ``requirement_level``, and once bound the
    ``evidence_link`` satisfying it. An item is ``satisfied`` only when its
    evidence resolves or a computed ``satisfied_when`` holds; a bare row stays
    ``missing``. Items that stop applying (the drug changed) are soft-deleted,
    never silently flipped.

    ``ruleset_version`` records the ruleset in force when the row was computed
    — the same contemporaneous-capture guarantee the encounter carries. The
    ledger is mutable only while the encounter is ``open``; after finalization
    both are frozen and corrections become dated addenda.

    Patient-scoped: ``patient_id`` gives it the auto-applied
    ``has_patient_access`` policy, like the rest of the chart.
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

#: What a ledger row IS, as distinct from how its charge attempt ended
#: (``CHARGE_STATUSES``). Before insurance there was one kind — the full-rate
#: charge for a visit — so rows predating this column read as ``session``,
#: hence the default.
#:
#: ``copay`` is the client's share taken at the visit; ``patient_resp`` what
#: the payer's remittance says they owe after adjudication. ``payment`` is
#: money collected against a bill somebody else raised, deliberately distinct
#: from ``session``: a session charge is itself a bill, so settling a
#: ``patient_resp`` with one would re-bill the amount it was paying off.
#: ``contractual_adjustment`` is the gap between the practice's rate and the
#: payer's allowed amount — a participating practice agrees never to bill it,
#: so it explains the arithmetic and is owed by nobody. ``write_off`` is money
#: the practice decides not to collect; ``credit`` money held on the client's
#: behalf, usually an over-collected copay.
#:
#: Only ``session`` and ``patient_resp`` are ever OWED; ``session``, ``copay``
#: and ``payment`` COLLECT. ``session`` is both, which is what makes a paid
#: self-pay visit net to zero. Nothing here encodes that — the arithmetic lives
#: in :func:`app.payments.balance.patient_balance`.
CHARGE_KINDS: tuple[str, ...] = (
    "session",
    "copay",
    "payment",
    "patient_resp",
    "contractual_adjustment",
    "write_off",
    "credit",
)

#: Why a practice stopped trying to collect. Required on a ``write_off`` row
#: and forbidden on every other kind: a write-off with no stated reason is the
#: one the practice cannot explain to an auditor months later, and a reason on
#: a copay means somebody set the wrong kind.
WRITE_OFF_REASONS: tuple[str, ...] = ("hardship", "small_balance", "courtesy", "error")

#: How the money arrived, for the kinds that collect it. ``card`` is a charge
#: this system put through the processor; everything else is money the practice
#: took by some means of its own and is recording after the fact.
#:
#: Deliberately a short closed set with ``other`` as the escape hatch rather
#: than an entry per instrument. Zelle, a card terminal the practice owns, an
#: employer's cheque for an EAP session and whatever replaces them next year
#: are all ``other`` plus a ``payment_reference``, which keeps the set from
#: growing a migration every time a practice meets a new one.
#:
#: This says how the money arrived, NOT what card is on file — that is
#: ``patient_payment_methods``, a different question with a different answer.
PAYMENT_METHODS: tuple[str, ...] = ("card", "cash", "check", "other")

#: The kinds a payment method can describe. The rest of ``CHARGE_KINDS`` are
#: statements about what is owed or forgiven rather than money changing hands:
#: a ``contractual_adjustment`` is an amount nobody ever pays, a ``write_off``
#: is money the practice decided not to collect, and a ``credit`` is a balance
#: held rather than a transfer. Asking how any of those was paid has no answer,
#: so the constraint below forbids the question rather than inventing one.
COLLECTING_CHARGE_KINDS: tuple[str, ...] = ("session", "copay", "payment")


class PatientPaymentMethodRow(Base):
    """The card a practice keeps on file for one client — processor ids only.

    A card number must never be able to reach this database, and that is a
    property of the schema rather than of the routes above it: there is no
    column here a PAN or CVC could be written into. The browser posts the card
    straight to Stripe against a SetupIntent and hands the backend a ``pm_…``
    id; stored is that id, the customer id, and the display triple the UI
    renders ("Visa ···· 4242, exp 4/2029").

    One row per client (``patient_id`` unique). Re-running setup replaces the
    card in place rather than accumulating stale ones to choose between; several
    cards per client is a client-portal concern, not a charge-for-the-session
    one.

    ``stripe_payment_method_id`` is nullable for one window: the row is created
    when the SetupIntent is minted (the customer id is known then) and completed
    when Stripe reports which payment method got attached. NULL means a setup
    started and never finished — not chargeable, and the charge route treats it
    as "no card on file".
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

    Written FIRST, as ``pending``, before Stripe is called — which is the point
    of the table. If the call times out or the process dies before the response,
    the practice still has a row saying a charge was attempted, reconcilable by
    ``stripe_payment_intent_id``. A ledger written only on success would lose
    exactly the cases somebody needs to look at.

    ``appointment_id`` is nullable and a soft reference with no foreign key: a
    charge need not hang off an appointment (a late-cancellation fee, a
    balance), and an appointment can be deleted while the money record must not.
    """

    __tablename__ = "patient_charges"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({_sql_in_list(CHARGE_STATUSES)})",
            name="ck_patient_charges_status",
        ),
        CheckConstraint(
            f"kind IN ({_sql_in_list(CHARGE_KINDS)})",
            name="ck_patient_charges_kind",
        ),
        # A write-off states its reason; nothing else carries one. Both halves
        # matter: an unexplained write-off is the row nobody can account for,
        # and a reason on a copay means the kind is wrong.
        CheckConstraint(
            "(kind = 'write_off') = (write_off_reason IS NOT NULL)",
            name="ck_patient_charges_write_off_reason_kind",
        ),
        CheckConstraint(
            f"write_off_reason IS NULL OR write_off_reason IN ({_sql_in_list(WRITE_OFF_REASONS)})",
            name="ck_patient_charges_write_off_reason",
        ),
        # A payment method is a fact about money arriving, so it exists on
        # exactly the kinds that collect and nowhere else. Both halves matter,
        # the same way they do for write_off_reason above: a method on a
        # contractual adjustment is a claim that somebody paid an amount
        # nobody ever pays, and a collecting row with no method cannot say how
        # the practice was paid — which is the whole point of the column.
        CheckConstraint(
            f"(kind IN ({_sql_in_list(COLLECTING_CHARGE_KINDS)})) = (method IS NOT NULL)",
            name="ck_patient_charges_method_kind",
        ),
        CheckConstraint(
            f"method IS NULL OR method IN ({_sql_in_list(PAYMENT_METHODS)})",
            name="ck_patient_charges_method",
        ),
        # An unlabelled "other" is the row nobody can account for six months
        # later — the same reasoning as ck_patient_charges_write_off_reason_kind.
        # Only ``other`` is held to it: cash needs no reference, and a card's
        # reference is its payment intent.
        CheckConstraint(
            "method IS DISTINCT FROM 'other' OR payment_reference IS NOT NULL",
            name="ck_patient_charges_other_has_reference",
        ),
        # Money is integer minor units and a charge is for a positive amount; a
        # refund is a status transition on this row, never a negative charge.
        # This holds for EVERY kind: a contractual adjustment and a credit are
        # stored as positive magnitudes and given their sign by the balance
        # arithmetic, so no reader has to remember which kinds are negative.
        CheckConstraint("amount_cents > 0", name="ck_patient_charges_amount_positive"),
        # The ledger read is "this client's charges, newest first".
        Index("ix_patient_charges_patient_created", "patient_id", "created_at"),
        # Remittance posting reads the other way round: "the rows this claim
        # produced". Partial — most rows never belong to a claim at all.
        Index(
            "ix_patient_charges_claim_id",
            "claim_id",
            postgresql_where=text("claim_id IS NOT NULL"),
        ),
        # The webhook finds its row by the PaymentIntent id; unique so a
        # replayed event can never fan out across two rows. Partial, because
        # many rows legitimately sit at NULL between the insert and the call.
        Index(
            "ux_patient_charges_payment_intent",
            "stripe_payment_intent_id",
            unique=True,
            postgresql_where=text("stripe_payment_intent_id IS NOT NULL"),
        ),
        # At most one balance payment in flight per client, and the database is
        # what says so. The route's own check is not enough: it reads the ledger
        # then inserts, so two requests can both read before either writes. A
        # staged row is ``pending``, and ``pending`` deliberately does not count
        # as collected — so while the first request is at the processor the
        # balance still reads as owed and the second charges the card again for
        # the whole of it. Two charges and a refund somebody has to notice.
        #
        # ``kind = 'payment'`` only: every other kind is a BILL rather than a
        # collection, and a client can legitimately have any number of those
        # outstanding. ``status = 'pending'`` only, so a terminal row never
        # blocks — a decline is final, and retrying is a fresh charge a
        # clinician asked for.
        Index(
            "ux_patient_charges_one_pending_payment",
            "patient_id",
            unique=True,
            postgresql_where=text("kind = 'payment' AND status = 'pending'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(128), primary_key=True)

    # Native uuid so the schema's ``has_patient_access`` policy applies.
    patient_id: Mapped[str] = mapped_column(Uuid(as_uuid=False), nullable=False)

    # Soft reference to ``appointments.id``, nullable — see the docstring.
    appointment_id: Mapped[str | None] = mapped_column(Uuid(as_uuid=False), nullable=True)

    # What this row IS. Defaulted in the database as well as here: every row
    # written before the column existed is a full-rate session charge, and the
    # default is what makes that true of the backfill and of any writer that
    # still does not name a kind.
    kind: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="session", default="session"
    )

    # The claim this row came out of, for the rows a remittance writes
    # (``patient_resp``, ``contractual_adjustment``). NULL on anything the
    # practice raised itself. ``SET NULL`` rather than ``CASCADE``: a deleted
    # claim must never take a money record with it.
    claim_id: Mapped[str | None] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("claims.id", ondelete="SET NULL"), nullable=True
    )

    # Required on ``write_off``, forbidden elsewhere — see ``WRITE_OFF_REASONS``.
    write_off_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # Free text the practice wrote about this row. Shown back to the practice,
    # never to a payer, and never logged: a clinician explaining a hardship
    # write-off will write clinical context into it.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How the money arrived — see ``PAYMENT_METHODS``. NULL on the kinds that
    # do not collect, which the table's constraints enforce rather than
    # leaving to callers.
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # How the practice can find this payment in its own records: a cheque
    # number, "Zelle 14 Mar", the terminal's reference. Short, and NOT
    # ``note``: this one appears on a statement the client may hand to their
    # payer, so it must stay a transaction identifier. Anything a clinician
    # would write about the client belongs in ``note``, which no payer sees.
    payment_reference: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # The charge that settled this row, for an owed row (``patient_resp``,
    # ``session``) paid off by a later collection. Soft reference to another
    # row of this same table — no foreign key, because the settling charge and
    # the settled row are written in either order depending on how the money
    # arrived.
    settled_by_charge_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

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
    coverage that names it. No ``user_id`` / ``patient_id`` to scope it by, so
    — like ``practice_billing_profile`` — its isolation boundary is the tenant
    schema and RLS is deliberately off (``_CORE_NOT_ROW_SCOPED``).

    ``payer_id`` is the electronic payer id on the card or in the
    clearinghouse's directory; ``clearinghouse_payer_id`` is the
    clearinghouse's own identifier for the same payer, NULL until something
    looks it up.

    Behavioral benefits are often administered by a different entity than the
    medical card names. ``is_carveout`` marks such a payer and ``carveout_of``
    points at the one it carves out from, so a claim routes to the right one.

    The three ``*_days`` columns are this payer's deadlines: original filing,
    corrected claim after a rejection, appeal after a denial. Nothing here
    computes a date from them; the claim workflow reads them.

    ``enrollment_status`` is the ELECTRONIC connection — whether 837/835/270
    can be exchanged with this payer. It says nothing about whether any
    clinician is on the payer's panel; that is
    ``payer_participations.status``, per clinician and independent. Both are
    true and false in every combination, and these similar names invite
    reading one for the other. Don't.

    The three ``enroll_*`` columns are the practice's answer to what Pablo
    should file with this payer. They gate enrollment, not traffic: a
    transaction the payer needs no enrollment for works whether its column is
    set or not — that is the payer's arrangement, not our permission. Which
    makes ``enroll_remittance`` the consequential one, since remittance
    always needs an enrollment and completing it moves the payer's ERAs to us
    from wherever they arrive today. So it starts off, and the other two
    start on: being wrong about remittance costs somebody downstream their
    payment postings, and being wrong about the other two costs a click.
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
    enroll_eligibility: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    enroll_claims: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true"), default=True
    )
    enroll_remittance: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    #: What the clearinghouse directory said this payer requires an enrollment
    #: for, comma-joined ("837P,835"), cached the last time we asked.
    #:
    #: NULL means nobody has asked yet. The EMPTY STRING is the load-bearing
    #: value: it means we asked and the answer was "nothing" — which is a very
    #: different state from "not enrolled yet", and without somewhere to record
    #: it the two are indistinguishable. A payer needing no enrollment would
    #: otherwise read as "Not enrolled" forever while she bills it happily.
    #:
    #: Cached rather than re-fetched because the alternative is a vendor call
    #: every time a payer list renders. Refreshed whenever we ask the directory
    #: for real, which is each time an enrollment is filed.
    directory_requires: Mapped[str | None] = mapped_column(String(40), nullable=True)
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

    Carries ``patient_id`` and no ``user_id``, so the standard
    ``has_patient_access`` policy applies: only a clinician with a grant on the
    client can read or write their coverage.

    One active primary coverage per client (partial unique index on
    ``patient_id WHERE active``). Replacing a plan deactivates the old row
    rather than deleting it, so a claim filed under the old plan can still be
    read against what was on file at the time.

    The member id is protected health information, not a secret: stored as
    typed, never in a log line. The subscriber fields are nullable because they
    only matter when the subscriber is not the client
    (``subscriber_relationship != 'self'``), and the claim scrub decides when
    they are required.

    ``last_271`` and ``verified_at`` are written by the eligibility check, never
    the chart form; NULL until one has run.
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
        # Money is integer minor units and an override is an amount somebody
        # collects, so it is positive. "Nothing to collect at the door" is
        # not an override of zero — it is no override plus a payer who
        # priced the benefit at nothing.
        CheckConstraint(
            "copay_override_cents IS NULL OR copay_override_cents > 0",
            name="ck_patient_coverage_copay_override_positive",
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
    # What the practice actually collects at the door, in cents, when it
    # knows better than the payer's answer: the figure printed on the card,
    # or the one the contract sets. NULL means "no override", not "no
    # copay" — the stored 271 is then the only answer there is.
    copay_override_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
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
    request per payer per transaction, and ``vendor_request_id`` is the
    clearinghouse's own id for it. Practice-level like ``payers`` — the practice
    is enrolled, not a clinician — so no ``user_id`` / ``patient_id`` and no
    ``id`` either, which keeps the table out of ``enable_rls_on_schema``
    entirely; its isolation boundary is the tenant schema.

    ``requested_by_user_id`` is who asked, and therefore who the reminder is
    addressed to when the payer wants something. Deliberately not named
    ``user_id``: that would make the row clinician-owned and hide the practice's
    enrollment from everyone else.

    ``instructions`` is the clearinghouse's wording of what the payer needs,
    shown on the payer row and in the reminder. Stored and rendered, never
    logged.
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
    # Held before filing for a person to read. Kept in step with
    # ``app.models.claims.ClaimState``: this tuple builds the ``ck_claims_state``
    # CHECK constraint below, so a state added to one and not the other is an
    # IntegrityError at the moment a claim tries to enter it.
    "in_review",
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

    A snapshot, not a view: the billing identity, subscriber and diagnosis list
    are copied in when the claim is built, so a later edit to the appointment or
    the coverage does not change what was filed. A claim past ``draft`` is never
    edited in place — the correction is a new row with ``frequency_code`` ``7``
    (or ``8`` to void) pointing back through ``parent_claim_id``.

    Carries ``patient_id`` and no ``user_id``, so the standard
    ``has_patient_access`` policy applies, same posture as ``patient_coverage``.
    The snapshots hold the subscriber's name, date of birth and address and the
    diagnosis codes — protected health information, stored here and never
    written to a log line.

    ``control_number`` is CLM01, the practice's own identifier for the claim on
    the wire. The clearinghouse and payer echo it back on every
    acknowledgement, which is how those match to this row. Generated here,
    unique within the practice, never reused.
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
    # The payer said what it did with the claim. One kind rather than three,
    # because paid / partially paid / denied is the claim's state and the
    # state already records it; what the receipt adds is the amounts and the
    # trace number behind that move.
    "adjudicated",
)


class ClaimEventRow(Base):
    """One receipt on a claim: a hop it took, or an alert raised about it.

    The claims tracker shows every hop with the moment its receipt arrived, and
    this is where those moments live. It is also what makes the acknowledgement
    paths idempotent: a clearinghouse webhook delivery is keyed by
    ``vendor_event_id`` (unique, so a redelivery cannot move the claim twice) and
    a deadline alert by ``(claim, kind, deadline kind, rung)`` (unique, so a
    restarted watchdog cannot re-raise it).

    Carries ``patient_id`` beside ``claim_id`` for the same reason
    ``claim_lines`` does: the row is isolated by its claim's
    ``has_patient_access`` policy without the policy engine learning a join.

    ``detail`` holds codes and vendor identifiers only — clearinghouse edit
    codes or 277CA status codes behind a rejection, correlation and trace ids
    behind a filing. Never a member id, a diagnosis or a name.
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

    Carries ``patient_id`` alongside ``claim_id`` so the row is isolated by the
    same ``has_patient_access`` policy as its claim, rather than by a join the
    policy engine would have to be taught. A copy of the claim's
    ``patient_id``, never different from it.

    ``allowed_cents`` / ``paid_cents`` / ``patient_resp_cents`` and
    ``adjustments`` (CARC/RARC entries) are written by remittance posting and
    stay unset until an 835 arrives.
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


#: Which of the 835's self-statements failed. See
#: ``app.models.claims_holds.HoldReason``, which is the definition; these
#: are here only so the check constraint can name the same set.
REMITTANCE_HOLD_REASONS: tuple[str, ...] = (
    "patient_responsibility",
    "line_balance",
    "claim_balance",
)

#: The states a remittance hold moves through. Named here so the check
#: constraint and the domain model's ``HoldState`` cannot drift apart
#: silently — ``app.models.claims_holds`` is the one that defines them.
REMITTANCE_HOLD_STATES: tuple[str, ...] = ("open", "acknowledged", "resolved")

#: How a hold ended. See ``app.models.claims_holds.HoldFinding``.
REMITTANCE_HOLD_FINDINGS: tuple[str, ...] = (
    "bill_as_stated",
    "waived",
    "parse_error",
    "payer_inconsistent",
)


class RemittanceHoldRow(Base):
    """A remittance whose two statements of the client's share disagree.

    An 835 states the claim's patient-responsibility total (``CLP05``) and then
    itemises the same figure across the service lines as ``PR``-group
    adjustments. When the two disagree the engine posts what the payer paid,
    withholds the client's ledger row, and writes one of these rather than
    billing a real person a number it cannot corroborate.

    Carries ``patient_id`` and no ``user_id``, so the standard
    ``has_patient_access`` policy applies and the clinician who owns the claim
    is the one who sees the hold.

    ``posting_key`` is the key the posting path already dedupes receipts on —
    the claim id plus the vendor entry that carried the adjudication — and is
    unique here for the same reason: a remittance delivered twice is one event,
    and a second hold would show one person the same disagreement twice.

    No column holds clinical content. ``codes`` is CARC/RARC pairs from a
    public list, the amounts are what the payer reported, and there is no name,
    date of service or diagnosis.
    """

    __tablename__ = "remittance_holds"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({_sql_in_list(REMITTANCE_HOLD_STATES)})",
            name="ck_remittance_holds_state",
        ),
        CheckConstraint(
            f"reason IN ({_sql_in_list(REMITTANCE_HOLD_REASONS)})",
            name="ck_remittance_holds_reason",
        ),
        # A line-level disagreement names its line; the two claim-level
        # reasons have no line to name. Without this a reader cannot tell
        # "the whole claim disagrees" from "we forgot to record which line".
        CheckConstraint(
            "(reason = 'line_balance') = (line_control_number IS NOT NULL)",
            name="ck_remittance_holds_line_reason",
        ),
        CheckConstraint(
            f"finding IS NULL OR finding IN ({_sql_in_list(REMITTANCE_HOLD_FINDINGS)})",
            name="ck_remittance_holds_finding",
        ),
        # A resolved hold says how it ended and when; an unresolved one says
        # neither. Both halves matter: a resolution with no finding is the
        # row nobody can account for, and a finding on an open hold means
        # somebody decided without the state following.
        CheckConstraint(
            "(state = 'resolved') = (resolved_at IS NOT NULL)",
            name="ck_remittance_holds_resolved_at_state",
        ),
        CheckConstraint(
            "(state = 'resolved') = (finding IS NOT NULL)",
            name="ck_remittance_holds_finding_state",
        ),
        UniqueConstraint("posting_key", name="ux_remittance_holds_posting_key"),
        # The tick reads "every hold still withholding a row", and the
        # resolved ones are the majority in the long run. Partial, so the
        # index stays the size of the work rather than the size of history.
        Index(
            "ix_remittance_holds_open",
            "state",
            "detected_at",
            postgresql_where=text("state <> 'resolved'"),
        ),
        Index("ix_remittance_holds_claim_id", "claim_id"),
        Index("ix_remittance_holds_patient_id", "patient_id"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    control_number: Mapped[str] = mapped_column(String(30), nullable=False)
    posting_key: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    reason: Mapped[str] = mapped_column(String(32), nullable=False)

    stated_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # What the payer says the client owes for this claim in total, NOT the
    # row that was withheld: the row is the difference from what the ledger
    # already carries, and that is a number whose home is the ledger. Signed
    # on purpose — a secondary payer paying off the primary's coinsurance
    # leaves a credit rather than a charge.
    patient_responsibility_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    line_control_number: Mapped[str | None] = mapped_column(String(30), nullable=True)

    codes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finding: Mapped[str | None] = mapped_column(String(24), nullable=True)


#: What a claim can put on somebody's list.
#:
#: NOT the same vocabulary as :data:`CLAIM_EVENT_KINDS` above, and the two are
#: deliberately separate. That one is the receipt ledger — every hop a claim
#: took, including the ones nobody acts on. This one is the subset a person has
#: to do something about.
#:
#: Two kinds from :class:`app.claims.events.ClaimEventKind` are missing, for
#: different reasons. ``paid`` needs nothing from anyone. And
#: ``enrollment_action_required`` has no claim behind it at all — it is raised
#: when a PAYER wants the practice to sign something, with a synthesised
#: ``claim_id`` and the vendor's request id for a control number — so it stays
#: a ``compliance_items`` row, where the practice's own obligations live. See
#: ``app.claims.events.NON_CLAIM_KINDS``.
CLAIM_REMINDER_KINDS: tuple[str, ...] = (
    "rejected",
    "denied",
    "partial",
    "stalled",
    "deadline_approaching",
    "deadline_missed",
    "unmatched_remittance",
    "remittance_held",
)


class ClaimReminderRow(Base):
    """One thing a claim needs a person to do, on the dashboard beside her licence.

    Claim alerts have appeared on the compliance dashboard since the pipeline
    learned to raise them, and they used to be ``compliance_items`` rows — a
    table whose every other row is about the clinician herself. That cost three
    things, and this table exists to get them back.

    **A real link to the claim.** The old row held the claim's control number in
    the first line of its ``notes`` and nowhere else, so finding the reminder
    for a claim meant a prefix match on free text. ``notes`` is editable
    through the compliance routes, which replace it wholesale, so a clinician
    tidying her own note severed the link — and the pipeline, finding nothing,
    filed a second reminder on the next tick, and every tick after that. The
    foreign key cannot be typed over.

    **A constraint instead of a convention.** One reminder per ``(claim, kind)``
    is now the database's rule rather than a read-then-write check with a race
    in the middle. Note this is coarser than :class:`ClaimEventRow`'s
    ``(claim, kind, deadline_kind, rung)`` on purpose: the ladder raises a
    receipt at fourteen days, seven, two and zero, and she wants one line about
    the deadline, not four.

    **The isolation its subject already has.** Carries ``patient_id`` and no
    ``user_id``, exactly as ``remittance_holds`` and ``claim_lines`` do, so the
    standard ``has_patient_access`` policy applies and the clinician who owns
    the claim is the one who sees the reminder. Every other row about a claim is
    isolated that way; this one was isolated by ``user_id`` instead, which meant
    a row naming a patient's claim followed a different rule from the claim.

    ``label`` and ``notes`` are a snapshot of what was true when the event
    landed — the payer's name, the codes it sent, its own wording of what it
    wants. They are not re-derived, because the point of a receipt is what it
    said at the time. ``notes`` stays editable; it simply no longer carries
    anything the system reads back.

    No clinical content. The label names a payer and a claim's control number,
    the notes carry CARC/RARC descriptions from a public list and the payer's
    instructions. No name, no diagnosis, no date of service.
    """

    __tablename__ = "claim_reminders"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ({_sql_in_list(CLAIM_REMINDER_KINDS)})",
            name="ck_claim_reminders_kind",
        ),
        UniqueConstraint("claim_id", "kind", name="ux_claim_reminders_claim_kind"),
        Index("ix_claim_reminders_patient_id", "patient_id"),
        Index("ix_claim_reminders_due_date", "due_date"),
    )

    id: Mapped[str] = mapped_column(Uuid(as_uuid=False), primary_key=True)
    claim_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    # A copy of the claim's, never different from it — the same arrangement
    # ``claim_lines`` documents, so the policy engine needs no join.
    patient_id: Mapped[str] = mapped_column(
        Uuid(as_uuid=False), ForeignKey("patients.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# The clinician's credential record — the structured facts behind the
# compliance clocks. ``compliance_items`` already tracks WHEN a licence
# expires; nothing recorded what the licence was, nor any of the
# biographical sections a payer application asks for.
#
# Three decisions hold across every table below:
#
#   * ``clinician_profiles`` keeps the PRIMARY identity (licence number and
#     state, DEA, NPI, taxonomy). These extend it, never fork it — there is
#     one DEA column in this schema and it lives there.
#   * Documents stay in ``compliance_documents``. Every table here points at
#     one by id; none stores bytes.
#   * Every table carries ``user_id``, so ``enable_rls_on_schema`` gives it
#     the direct-ownership policy — which is why none needs registering as
#     not-row-scoped. They are row-scoped, on the clinician who owns them.
#
# This is provider PII (SSN, date of birth, bank account), not patient PHI,
# and still the most sensitive class in the schema. The encrypted columns are
# read through one audited path (``app.credentialing.government_ids``).
# ---------------------------------------------------------------------------
