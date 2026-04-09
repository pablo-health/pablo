# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Audit logging models for HIPAA compliance."""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class AuditAction(StrEnum):
    """Actions tracked in audit log for HIPAA compliance."""

    # Patient operations
    PATIENT_CREATED = "patient_created"
    PATIENT_LISTED = "patient_listed"
    PATIENT_VIEWED = "patient_viewed"
    PATIENT_UPDATED = "patient_updated"
    PATIENT_DELETED = "patient_deleted"
    PATIENT_EXPORTED = "patient_exported"

    # Session operations
    SESSION_CREATED = "session_created"
    SESSION_LISTED = "session_listed"
    SESSION_VIEWED = "session_viewed"
    SESSION_FINALIZED = "session_finalized"
    SESSION_RATING_UPDATED = "session_rating_updated"

    # Admin operations
    EXPORT_QUEUE_VIEWED = "export_queue_viewed"
    EXPORT_ACTION_TAKEN = "export_action_taken"

    # Tenant management
    TENANT_LISTED = "tenant_listed"
    TENANT_VIEWED = "tenant_viewed"
    TENANT_DISABLED = "tenant_disabled"
    TENANT_ENABLED = "tenant_enabled"
    TENANT_DELETED = "tenant_deleted"

    # EHR navigation
    EHR_NAVIGATE = "ehr_navigate"


class ResourceType(StrEnum):
    """Resource types for audit logging."""

    PATIENT = "patient"
    SESSION = "session"
    EHR_ROUTE = "ehr_route"


# Retention period for audit logs (HIPAA requires minimum 6 years, but
# 180 days is sufficient for breach investigation in pilot phase)
AUDIT_LOG_RETENTION_DAYS = 180


@dataclass
class AuditLogEntry:
    """
    Audit log entry for HIPAA compliance tracking.

    Denormalized design stores user/patient names at time of action
    for standalone queryability without joins.
    """

    # Auto-generated fields
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z")
    )
    expires_at: str = field(
        default_factory=lambda: (
            (datetime.now(UTC) + timedelta(days=AUDIT_LOG_RETENTION_DAYS))
            .isoformat()
            .replace("+00:00", "Z")
        )
    )

    # Who performed the action
    user_id: str = ""
    user_email: str = ""
    user_name: str = ""

    # What action was performed
    action: str = ""  # AuditAction value
    resource_type: str = ""  # ResourceType value
    resource_id: str = ""

    # Denormalized context for readability
    patient_id: str | None = None
    patient_name: str | None = None
    session_id: str | None = None

    # Request context
    ip_address: str | None = None
    user_agent: str | None = None

    # For updates: what changed (optional)
    changes: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for Firestore storage."""
        data = asdict(self)
        # Remove None values for cleaner Firestore documents
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditLogEntry":
        """Create AuditLogEntry from Firestore document."""
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            expires_at=data["expires_at"],
            user_id=data["user_id"],
            user_email=data["user_email"],
            user_name=data["user_name"],
            action=data["action"],
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            patient_id=data.get("patient_id"),
            patient_name=data.get("patient_name"),
            session_id=data.get("session_id"),
            ip_address=data.get("ip_address"),
            user_agent=data.get("user_agent"),
            changes=data.get("changes"),
        )
