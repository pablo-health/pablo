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
    # cron (THERAPY-ab7). Emitted by the SaaS
    # ``saas.bin.audio_retention_purge`` entrypoint. The value is defined
    # here (not in the SaaS overlay) so audit-log readers and dashboards
    # render it consistently on both tiers.
    AUDIO_PURGED = "audio_purged"

    # iCal sync / EHR client import operations
    CLIENT_RESOLVED = "client_resolved"
    CLIENTS_IMPORTED = "clients_imported"

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

    # EHR navigation
    EHR_NAVIGATE = "ehr_navigate"

    # User reading their own audit trail (meta-audit).
    SELF_AUDIT_VIEWED = "self_audit_viewed"


class ResourceType(StrEnum):
    """Resource types for audit logging."""

    PATIENT = "patient"
    SESSION = "session"
    APPOINTMENT = "appointment"
    EHR_ROUTE = "ehr_route"
    SELF = "self"
    TENANT_EXPORT = "tenant_export"


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
