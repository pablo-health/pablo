# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Platform audit log models — cross-tenant admin stream, PHI-free."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from ..utcnow import utc_now_iso
from .audit import AUDIT_LOG_RETENTION_DAYS


class PlatformAuditAction(StrEnum):
    TENANT_PROVISIONED = "tenant_provisioned"
    TENANT_DEPROVISIONED = "tenant_deprovisioned"
    PENTEST_TENANT_PROVISIONED = "pentest_tenant_provisioned"
    PENTEST_TENANT_DEPROVISIONED = "pentest_tenant_deprovisioned"
    # Pentest *run* lifecycle (distinct from the per-test-tenant
    # provision/deprovision pair above). Emitted by the pentest
    # runner itself once per Cloud Run Job execution, so the platform
    # audit stream answers "what security testing has run against
    # this project, when, and what did it find" without needing to
    # cross-correlate Cloud Run logs and the compliance bucket.
    PENTEST_RUN_STARTED = "pentest_run_started"
    PENTEST_RUN_COMPLETED = "pentest_run_completed"


class PlatformResourceType(StrEnum):
    TENANT = "tenant"
    # PENTEST_RUN is platform-scoped; its resource_id is the
    # 8-char run UUID the runner generates.
    PENTEST_RUN = "pentest_run"


@dataclass
class PlatformAuditLogEntry:
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
