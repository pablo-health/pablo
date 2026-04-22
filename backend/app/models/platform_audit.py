# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Platform-level audit logging models.

Platform audit is a cross-tenant stream for administrative operations
that don't belong to any single practice: tenant provisioning, pentest
tenant lifecycle, allowlist edits, flag toggles. Kept separate from
per-tenant ``<practice>.audit_logs`` (ePHI access) so the two streams
can be retained, granted, and reviewed independently.

PHI-free by construction — the actors are operators/runners, the
resources are tenants and config rows, never patients or sessions.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from ..utcnow import utc_now_iso
from .audit import AUDIT_LOG_RETENTION_DAYS


class PlatformAuditAction(StrEnum):
    """Administrative actions tracked in the platform audit stream."""

    # Real-tenant lifecycle
    TENANT_PROVISIONED = "tenant_provisioned"
    TENANT_DEPROVISIONED = "tenant_deprovisioned"

    # Pentest-tenant lifecycle — expected exactly once per weekly run.
    # Any unexpected occurrence is a HIGH-severity signal.
    PENTEST_TENANT_PROVISIONED = "pentest_tenant_provisioned"
    PENTEST_TENANT_DEPROVISIONED = "pentest_tenant_deprovisioned"


class PlatformResourceType(StrEnum):
    """Resource types for the platform audit stream."""

    TENANT = "tenant"


@dataclass
class PlatformAuditLogEntry:
    """One platform-layer audit event.

    ``details`` stores non-PHI structured context (schema names, flag
    values, reason codes). Never accepts PHI — callers are expected to
    keep this stream PHI-free, same contract as the per-tenant audit
    log. A linter check on ``PHI_FIELD_NAMES`` guards the dict keys.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=utc_now_iso)
    expires_at: str = field(
        default_factory=lambda: (
            (datetime.now(UTC) + timedelta(days=AUDIT_LOG_RETENTION_DAYS))
            .isoformat()
            .replace("+00:00", "Z")
        )
    )

    actor_user_id: str = ""
    action: str = ""
    resource_type: str = ""
    resource_id: str = ""

    tenant_schema: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {k: v for k, v in data.items() if v is not None}
