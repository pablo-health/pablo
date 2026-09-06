# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit logging models for HIPAA compliance."""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from ..utcnow import utc_now_iso


class AuditAction(StrEnum):
    """Actions tracked in audit log for HIPAA compliance."""

    # Patient operations
    PATIENT_CREATED = "patient_created"
    PATIENT_LISTED = "patient_listed"
    PATIENT_VIEWED = "patient_viewed"
    PATIENT_UPDATED = "patient_updated"
    PATIENT_DELETED = "patient_deleted"
    PATIENT_RESTORED = "patient_restored"
    PATIENT_EXPORTED = "patient_exported"
    PATIENT_PURGED = "patient_purged"
    # Chart closure (THERAPY-hek). Orthogonal to soft-delete: closing a
    # chart marks the clinical/administrative care episode as ended, but
    # the row stays live and the day-30 hard-purge clock (THERAPY-cgy)
    # is not advanced.
    CHART_CLOSED = "chart_closed"
    CHART_REOPENED = "chart_reopened"

    # Session operations
    SESSION_CREATED = "session_created"
    SESSION_LISTED = "session_listed"
    SESSION_VIEWED = "session_viewed"
    SESSION_UPDATED = "session_updated"
    SESSION_FINALIZED = "session_finalized"
    SESSION_RATING_UPDATED = "session_rating_updated"
    SESSION_TRANSCRIPT_UPLOADED = "session_transcript_uploaded"
    SESSION_AUDIO_UPLOADED = "session_audio_uploaded"
    # The SOAP note (clinical PHI) is written by the off-request generation
    # worker, not on the upload request — so the note's creation is audited
    # there, at the point the PHI actually comes into existence.
    SESSION_NOTE_GENERATED = "session_note_generated"
    # Recorded session audio deleted by the per-practice audio retention
    # cron (THERAPY-ab7). The action value is defined here so audit-log
    # readers and dashboards render it consistently regardless of which
    # job entrypoint emitted the row.
    AUDIO_PURGED = "audio_purged"

    # iCal sync / EHR client import operations
    CLIENT_RESOLVED = "client_resolved"
    CLIENTS_IMPORTED = "clients_imported"
    # A sync run that surfaces unmatched external calendar events — each
    # carries a client_identifier (an external client name), so the read is
    # PHI-adjacent. The `changes` payload stays PHI-free (counts only); the
    # identifiers themselves are never recorded.
    ICAL_CALENDAR_SYNCED = "ical_calendar_synced"
    # A therapist attesting that the Google account they connected is
    # covered by an agreement their own practice holds, which is what
    # permits a patient's name to be written onto that calendar. Recorded
    # as evidence rather than as a preference: it says who attested, when,
    # and which calendar account it covered, and it outlives the
    # connection it was made about.
    CALENDAR_NAME_DISCLOSURE_ATTESTED = "calendar_name_disclosure_attested"

    # Appointment operations
    APPOINTMENT_CREATED = "appointment_created"
    APPOINTMENT_LISTED = "appointment_listed"
    APPOINTMENT_VIEWED = "appointment_viewed"
    APPOINTMENT_UPDATED = "appointment_updated"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_SERIES_CREATED = "appointment_series_created"
    APPOINTMENT_SERIES_UPDATED = "appointment_series_updated"
    APPOINTMENT_SERIES_CANCELLED = "appointment_series_cancelled"

    # Admin operations
    EXPORT_QUEUE_VIEWED = "export_queue_viewed"
    EXPORT_ACTION_TAKEN = "export_action_taken"
    TENANT_EXPORTED = "tenant_exported"

    # Tenant management
    TENANT_LISTED = "tenant_listed"
    TENANT_VIEWED = "tenant_viewed"
    TENANT_DISABLED = "tenant_disabled"
    TENANT_ENABLED = "tenant_enabled"
    TENANT_DELETED = "tenant_deleted"
    # Offboarding scheduled but tenant is still active during the grace
    # window. Distinct from TENANT_DISABLED (which implies access cut)
    # and TENANT_DELETED (which implies the schema is gone).
    TENANT_OFFBOARD_SCHEDULED = "tenant_offboard_scheduled"

    # Tenant invitations — minted by platform admins to onboard a new
    # owner without a marketing-checkout round-trip. Lives in OSS so
    # a downstream deployment's overlay can log via the type-safe
    # AuditService API rather than persisting raw strings.
    INVITATION_CREATED = "invitation_created"
    INVITATION_REISSUED = "invitation_reissued"
    INVITATION_REVOKED = "invitation_revoked"
    INVITATION_ACCEPTED = "invitation_accepted"
    INVITATION_EMAIL_SWITCHED = "invitation_email_switched"

    # Practice/tenant configuration writes. Used for retention-policy
    # changes (e.g. per-practice audio retention slider — THERAPY-6k7)
    # and any future configurable retention surfaces. The `changes`
    # payload carries {field, previous, new} so audit readers can
    # reconstruct what was modified without storing PHI.
    RETENTION_UPDATED = "retention_updated"

    # EHR navigation
    EHR_NAVIGATE = "ehr_navigate"

    # User reading their own audit trail (meta-audit).
    SELF_AUDIT_VIEWED = "self_audit_viewed"

    # Per-user opt-in for chat quality review (THERAPY-8biz / opt-in
    # content capture). Recorded on every state change so the user can
    # see their own consent history. Payload carries no PHI — the
    # `changes` dict is the bare new state, e.g. ``{"opt_in": true}``.
    CHAT_QUALITY_REVIEW_OPT_IN = "chat_quality_review_opt_in"
    CHAT_QUALITY_REVIEW_OPT_OUT = "chat_quality_review_opt_out"
    CHAT_QUALITY_REVIEW_PURGE_REQUESTED = "chat_quality_review_purge_requested"

    # Per-user opt-in for quality review of session-derived notes
    # (session transcript + generated note text). Separate from the chat
    # consent above because session-derived content is a distinct surface;
    # recorded on every state change. Same no-PHI payload rule — `changes`
    # carries the bare new state, e.g. ``{"opt_in": true}``.
    SESSION_NOTES_QUALITY_REVIEW_OPT_IN = "session_notes_quality_review_opt_in"
    SESSION_NOTES_QUALITY_REVIEW_OPT_OUT = "session_notes_quality_review_opt_out"
    SESSION_NOTES_QUALITY_REVIEW_PURGE_REQUESTED = "session_notes_quality_review_purge_requested"

    # Per-user opt-in for quality review of email-reply drafting (the inbound
    # message plus the AI-drafted reply the clinician then edits). Separate from
    # chat and session-notes above because email correspondence is a distinct
    # surface a clinician may allow independently. Same no-PHI payload rule —
    # `changes` carries the bare new state, e.g. ``{"opt_in": true}``.
    INBOX_QUALITY_REVIEW_OPT_IN = "inbox_quality_review_opt_in"
    INBOX_QUALITY_REVIEW_OPT_OUT = "inbox_quality_review_opt_out"
    INBOX_QUALITY_REVIEW_PURGE_REQUESTED = "inbox_quality_review_purge_requested"

    # Onboarding milestones. Recorded regardless of whether PHI has been
    # touched — these are compliance events (BAA is a legal agreement, MFA
    # is a security control, security guide is a HIPAA § 164.308(a)(5)
    # training acknowledgment). Useful for detecting onboarding abandonment
    # (any user with onboarding_started but no baa_accepted row is stalled).
    ONBOARDING_STARTED = "onboarding_started"
    ONBOARDING_BAA_ACCEPTED = "onboarding_baa_accepted"
    ONBOARDING_MFA_ENROLLED = "onboarding_mfa_enrolled"
    ONBOARDING_SECURITY_GUIDE_ACKNOWLEDGED = "onboarding_security_guide_acknowledged"
    ONBOARDING_COMPLETED = "onboarding_completed"

    # Account-recovery / authentication security events (HIPAA
    # § 164.308(a)(5)(ii)(C) login monitoring, § 164.312(b) audit controls).
    # A one-time backup code redeemed as the second factor is the highest-value
    # recovery path in the system — it mints an MFA-satisfied session, so the
    # event needs a durable, queryable record, not just an app log line.
    RECOVERY_CODE_REDEEMED = "recovery_code_redeemed"
    # A passwordless passkey assertion that minted a session. The primary
    # passwordless sign-in event, audited alongside recovery so login
    # monitoring covers the whole authenticator surface.
    PASSKEY_AUTHENTICATED = "passkey_authenticated"

    # Patient-context chat (THERAPY-bhv). Two-tier audit policy per
    # docs/architecture/patient-context-chat-oss.md §10: lifecycle events
    # land in the audit log; per-turn detail lives on chat_messages rows.
    CHAT_CONVERSATION_CREATED = "chat_conversation_created"
    # Read-access event for the conversation-detail endpoint, which returns
    # full message bodies. Mirrors PATIENT_VIEWED / SESSION_VIEWED so chat
    # reads are audited like every other PHI-read content surface — record-level
    # ("which conversation").
    CHAT_CONVERSATION_VIEWED = "chat_conversation_viewed"
    # Read-access event for the conversation-LIST endpoint. The list surfaces no
    # message bodies, but each item carries a title that defaults to
    # "Chat about {patient_display_name}" — an identifier disclosure. So the list
    # is audited patient-scoped ("that the patient's chat index was viewed",
    # resource_id = patient_id), one row per patient per window, NOT one per
    # conversation: granularity matches what was disclosed (titles), not bodies.
    CHAT_CONVERSATION_LIST_VIEWED = "chat_conversation_list_viewed"
    CHAT_CONVERSATION_ARCHIVED = "chat_conversation_archived"
    CHAT_CONVERSATION_PURGED = "chat_conversation_purged"
    CHAT_CHART_PROMOTION = "chat_chart_promotion"
    CHAT_TURN_BLOCKED = "chat_turn_blocked"

    # Patient document upload (THERAPY-ak6m.2). UPLOAD_INITIATED fires
    # when a signed PUT URL is minted and the placeholder row inserted;
    # UPLOADED fires after the finalize step verifies the GCS object,
    # validates size/mime, and runs PyMuPDF text extraction. VIEWED,
    # DOWNLOADED, DELETED cover the read-side lifecycle. Payloads carry
    # ids + size + mime + category only — never filename or
    # extracted_text content.
    #
    # *_RESTRICTED variants fire for documents in the therapist_private
    # or psychotherapy_notes categories — both uploader-only, both
    # outside the standard patient-record release path. Splitting the
    # action lets compliance dashboards report on sensitive-document
    # access independently of the chart traffic, and gives us a hook
    # for category-specific retention policies later.
    PATIENT_DOCUMENT_UPLOAD_INITIATED = "patient_document_upload_initiated"
    PATIENT_DOCUMENT_UPLOADED = "patient_document_uploaded"
    PATIENT_DOCUMENT_VIEWED = "patient_document_viewed"
    PATIENT_DOCUMENT_VIEWED_RESTRICTED = "patient_document_viewed_restricted"
    PATIENT_DOCUMENT_DOWNLOADED = "patient_document_downloaded"
    PATIENT_DOCUMENT_DOWNLOADED_RESTRICTED = "patient_document_downloaded_restricted"
    PATIENT_DOCUMENT_DELETED = "patient_document_deleted"
    PATIENT_DOCUMENT_OCR_INVOKED = "patient_document_ocr_invoked"

    # Companion audio signed-URL upload (additive to the existing
    # multipart /upload-audio surface — companion app migrates at its
    # own pace). INIT fires when channel signed URLs are minted;
    # UPLOADED fires after finalize verifies both channel blobs land
    # and enqueues transcription. The existing SESSION_AUDIO_UPLOADED
    # event remains in use by the multipart path so audit dashboards
    # don't fragment.
    SESSION_AUDIO_UPLOAD_INITIATED = "session_audio_upload_initiated"

    # Companion handoff (companion-thin-client.md). Emitted when a desktop
    # companion redeems a single-use launch intent and the backend
    # discloses the appointment's patient name + video URL to it. Logged
    # at record-level granularity (a patient name is disclosed) with the
    # patient association carried by the `patient=` argument; the `changes`
    # payload never carries patient_name, video_url, or the raw intent_id.
    LAUNCH_INTENT_REDEEMED = "launch_intent_redeemed"

    # Self-pay card payments. These are financial records about a named
    # client, so reading or writing one is a patient-record access and is
    # audited as such. PATIENT_CHARGE_CREATED is emitted when the clinician
    # initiates the charge, not when the processor answers: the event is "this
    # clinician asked to charge this client", which is true the moment the
    # ledger row exists. The outcome lives on that row, and the `changes`
    # payload carries only its opaque id.
    PATIENT_PAYMENT_SETUP_STARTED = "patient_payment_setup_started"
    PATIENT_PAYMENT_METHOD_STORED = "patient_payment_method_stored"
    PATIENT_PAYMENT_METHOD_VIEWED = "patient_payment_method_viewed"
    PATIENT_CHARGE_CREATED = "patient_charge_created"
    PATIENT_CHARGES_VIEWED = "patient_charges_viewed"
    # Reading what a client would be charged discloses their rate — a
    # financial fact about a named person — so the preview is audited like
    # any other read of the record, separately from the charge itself.
    PATIENT_CHARGE_AMOUNT_VIEWED = "patient_charge_amount_viewed"

    # Coverage on file. A client's plan — payer, member id, subscriber — is
    # protected health information about a named person, so reading or
    # writing it is a patient-record access and is audited as such. The
    # `changes` payload carries the coverage row id and the payer row id
    # only: never the member id or anything about the subscriber.
    PATIENT_COVERAGE_VIEWED = "patient_coverage_viewed"
    PATIENT_COVERAGE_CREATED = "patient_coverage_created"
    PATIENT_COVERAGE_UPDATED = "patient_coverage_updated"
    PATIENT_COVERAGE_DEACTIVATED = "patient_coverage_deactivated"
    # An eligibility check discloses the client to the payer; one row per check.
    PATIENT_COVERAGE_VERIFIED = "patient_coverage_verified"

    # Claims. A claim carries the client's diagnoses and the subscriber's
    # demographics, so building, reading or moving one is a patient-record
    # access. The `changes` payload carries the claim id, its control number,
    # its state, the payer row id and — for a correction or void — the parent
    # claim id. Never a member id, a diagnosis code or anything about the
    # subscriber.
    CLAIM_CREATED = "claim_created"
    CLAIM_VIEWED = "claim_viewed"
    CLAIM_VALIDATED = "claim_validated"
    CLAIM_CORRECTED = "claim_corrected"
    CLAIM_VOIDED = "claim_voided"
    PATIENT_CLAIMS_VIEWED = "patient_claims_viewed"
    # The biller handoff: a range of claims left the practice as a CSV, or
    # one claim as a CMS-1500-layout PDF. A disclosure, so the CSV row names
    # every claim it carried (ids and control numbers, with the range and
    # the count) and the PDF row names its one claim.
    CLAIMS_EXPORTED = "claims_exported"
    CLAIM_EXPORTED = "claim_exported"

    # Superbills. The document hands the client their diagnoses, the
    # services and the practice's identity to pass on to an insurer, so
    # issuing one is a disclosure. The `changes` payload carries the period,
    # the claim, line and charge ids the document was rendered from — or,
    # when it was refused, the codes and field paths of what was missing.
    SUPERBILL_GENERATED = "superbill_generated"
    SUPERBILL_REFUSED = "superbill_refused"


class ResourceType(StrEnum):
    """Resource types for audit logging."""

    PATIENT = "patient"
    SESSION = "session"
    APPOINTMENT = "appointment"
    EHR_ROUTE = "ehr_route"
    SELF = "self"
    TENANT_EXPORT = "tenant_export"
    CHAT_CONVERSATION = "chat_conversation"
    PATIENT_DOCUMENT = "patient_document"
    INVITATION = "invitation"
    CLAIM = "claim"
    CLAIM_EXPORT = "claim_export"


# HIPAA § 164.316(b)(2)(i) — 6-year minimum retention. 7y = margin + matches
# typical state medical-record retention laws.
AUDIT_LOG_RETENTION_DAYS = 2555


# Field names whose *values* must never appear in audit_logs. The set is
# consumed by tests and by AuditRepository.metadata_for_review() to assert
# that the audit table (and any payload derived from it) stays PHI-free.
PHI_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "user_name",
        "user_email",
        "patient_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "date_of_birth",
        "dob",
        "diagnosis",
        "address",
        "ssn",
        "mrn",
    }
)


# What kind of principal performed an audited action.
#
# ``clinician`` is a signed-in practitioner acting in their own practice.
# ``patient`` is a patient principal acting for themselves — consent decisions
# above all, which is what makes the distinction legally load-bearing rather
# than cosmetic.
# ``anonymous`` is an unauthenticated principal acting through a public surface
# (today: a booking link). For this kind, ``user_id`` names the SCOPE principal
# — the clinician whose RLS context the write happened under — not the actor;
# the actor is identified by ``ip_address`` plus the provenance in ``changes``.
# ``system`` is automated work with no human in the loop: a cron, a queue
# worker, a background agent. ``user_id`` again names the SCOPE principal (the
# clinician whose data was touched), NOT an actor — nobody clicked anything.
# ``actor_component`` says which part of the system acted, and rows of this
# kind should always carry it.
# ``platform_staff`` is an operator of this deployment reading or changing a
# practice's data from outside that practice — support and break-glass access.
# Here ``user_id`` IS the actor: the staff member is individually accountable.
#
# The last two exist because a reader years later has to be able to tell
# "the therapist opened this chart" from "a background job read it to compose
# an email" from "someone at the vendor looked". Folding all three into
# ``clinician`` made the log claim a practitioner did things they never did.
ACTOR_TYPE_CLINICIAN = "clinician"
ACTOR_TYPE_PATIENT = "patient"
ACTOR_TYPE_ANONYMOUS = "anonymous"
ACTOR_TYPE_SYSTEM = "system"
ACTOR_TYPE_PLATFORM_STAFF = "platform_staff"
ACTOR_TYPES: tuple[str, ...] = (
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    ACTOR_TYPE_ANONYMOUS,
    ACTOR_TYPE_SYSTEM,
    ACTOR_TYPE_PLATFORM_STAFF,
)

# Which part of the system acted, for ``actor_type == "system"`` rows.
#
# Deliberately a free string with constants rather than a DB-constrained enum:
# a new background job should not need a schema migration to be able to audit
# itself, and overlays that live outside this package need to name their own
# components without dragging values through here. Consumers treat it as an
# opaque label for filtering. Dotted, stable, and never PHI.
ACTOR_COMPONENT_MAX_LENGTH = 64


@dataclass
class AuditLogEntry:
    """
    Audit log entry for HIPAA compliance tracking.

    PHI-free by design. No denormalized names, emails, or free-text clinical
    data. The `changes` field stores field-name diffs for UPDATE actions
    (e.g. ``{"changed_fields": ["first_name", "diagnosis"]}``) — never the
    old/new values themselves.
    """

    # Auto-generated fields
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=utc_now_iso)
    expires_at: str = field(
        default_factory=lambda: (
            (datetime.now(UTC) + timedelta(days=AUDIT_LOG_RETENTION_DAYS))
            .isoformat()
            .replace("+00:00", "Z")
        )
    )

    # Who performed the action.
    #
    # ``user_id`` is the actor identifier as recorded; ``actor_type`` says what
    # KIND of actor it names. Both ids are uuids, so without the discriminator a
    # row cannot answer "was this the clinician or the patient?" without joining
    # two tables and hoping exactly one matches — and this is the six-year
    # record, read years later by someone in a dispute.
    #
    # Defaults to ``clinician`` so every row written before this existed, and
    # every caller that does not set it, keeps exactly the meaning it had.
    user_id: str = ""
    actor_type: str = ACTOR_TYPE_CLINICIAN
    # Which part of the system acted. Only meaningful for ``system`` rows,
    # where ``user_id`` names the scope rather than an actor; ``None`` for
    # every human kind, whose actor is already named by ``user_id``.
    actor_component: str | None = None

    # What action was performed
    action: str = ""  # AuditAction value
    resource_type: str = ""  # ResourceType value
    resource_id: str = ""

    # Opaque context IDs (non-PHI)
    patient_id: str | None = None
    session_id: str | None = None

    # Request context
    ip_address: str | None = None
    user_agent: str | None = None

    # Non-PHI structured data only: field-name diffs, counts, enum transitions.
    # Callers must never put PHI values here. AuditService enforces this via
    # the PHI_FIELD_NAMES assertion.
    changes: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditLogEntry":
        """Create AuditLogEntry from dictionary."""
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            expires_at=data["expires_at"],
            user_id=data["user_id"],
            actor_type=data.get("actor_type", ACTOR_TYPE_CLINICIAN),
            action=data["action"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            patient_id=data.get("patient_id"),
            session_id=data.get("session_id"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            changes=data.get("changes"),
        )
