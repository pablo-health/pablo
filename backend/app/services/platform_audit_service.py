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

if TYPE_CHECKING:
    from fastapi import Request

logger = logging.getLogger(__name__)


class PlatformAuditService:
    def __init__(self, repo: PlatformAuditRepository) -> None:
        self._repo = repo

    def _extract_request_context(
        self, request: Request | None
    ) -> tuple[str | None, str | None]:
        if request is None:
            return None, None
        ip = request.headers.get("X-Forwarded-For")
        if ip:
            ip = ip.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else None
        return ip, request.headers.get("User-Agent")

    def log_tenant_action(
        self,
        action: PlatformAuditAction,
        actor_user_id: str,
        tenant_schema: str,
        tenant_id: str,
        request: Request | None = None,
        details: dict[str, Any] | None = None,
    ) -> PlatformAuditLogEntry:
        ip, ua = self._extract_request_context(request)
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
        from ..db import get_db_session  # noqa: PLC0415
        from ..repositories.postgres.platform_audit import (  # noqa: PLC0415
            PostgresPlatformAuditRepository,
        )

        return PlatformAuditService(PostgresPlatformAuditRepository(get_db_session()))
    except RuntimeError:
        return PlatformAuditService(InMemoryPlatformAuditRepository())
