# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Platform audit service — cross-tenant admin event stream."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..models.platform_audit import (
    PlatformAuditAction,
    PlatformAuditLogEntry,
    PlatformResourceType,
)
from ..repositories.platform_audit import (
    InMemoryPlatformAuditRepository,
    PlatformAuditRepository,
)
from ..request_context import extract_request_context

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


class PlatformAuditService:
    def __init__(self, repo: PlatformAuditRepository) -> None:
        self._repo = repo

    def log_pentest_run(
        self,
        action: PlatformAuditAction,
        run_uuid: str,
        actor_user_id: str,
        details: dict[str, Any] | None = None,
    ) -> PlatformAuditLogEntry:
        """Emit a platform-level audit entry for a pentest run lifecycle event.

        Called by the pentest runner (Cloud Run Job, no HTTP request
        context) at start and end of every execution. Tenant-scoped
        fields are intentionally null — these are platform-level
        events, not per-tenant. ``run_uuid`` is the 8-char identifier
        the runner generates so the started/completed pair can be
        correlated.
        """
        entry = PlatformAuditLogEntry(
            actor_user_id=actor_user_id,
            action=action.value,
            resource_type=PlatformResourceType.PENTEST_RUN.value,
            resource_id=run_uuid,
            tenant_schema=None,
            ip_address=None,
            user_agent=None,
            details=details,
        )
        try:
            self._repo.append(entry)
        except Exception:
            logger.exception(
                "Failed to persist platform audit entry id=%s action=%s",
                entry.id,
                entry.action,
            )
            raise
        return entry

    def log_tenant_action(
        self,
        action: PlatformAuditAction,
        actor_user_id: str,
        tenant_schema: str,
        tenant_id: str,
        request: Request | None = None,
        details: dict[str, Any] | None = None,
    ) -> PlatformAuditLogEntry:
        ip, ua = extract_request_context(request)
        entry = PlatformAuditLogEntry(
            actor_user_id=actor_user_id,
            action=action.value,
            resource_type=PlatformResourceType.TENANT.value,
            resource_id=tenant_id,
            tenant_schema=tenant_schema,
            ip_address=ip,
            user_agent=ua,
            details=details,
        )
        try:
            self._repo.append(entry)
        except Exception:
            logger.exception(
                "Failed to persist platform audit entry id=%s action=%s",
                entry.id,
                entry.action,
            )
            raise
        return entry


def get_platform_audit_service() -> PlatformAuditService:
    # In-memory fallback for dev/test harnesses without Postgres; never
    # production — entries are lost on restart.
    try:
        from ..db import get_db_session
        from ..repositories.postgres.platform_audit import (
            PostgresPlatformAuditRepository,
        )

        return PlatformAuditService(PostgresPlatformAuditRepository(get_db_session()))
    except RuntimeError:
        return PlatformAuditService(InMemoryPlatformAuditRepository())
