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
    # the SaaS overlay can log via the type-safe AuditService API
    # rather than persisting raw strings.
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

    # Who performed the action
    user_id: str = ""

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
            action=data["action"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            patient_id=data.get("patient_id"),
            session_id=data.get("session_id"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            changes=data.get("changes"),
        )
