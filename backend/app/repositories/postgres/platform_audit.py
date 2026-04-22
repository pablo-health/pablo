# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL platform audit log repository."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import desc, select

from ...db.platform_models import PlatformAuditLogRow
from ..platform_audit import PlatformAuditRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...models.platform_audit import PlatformAuditLogEntry


class PostgresPlatformAuditRepository(PlatformAuditRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, entry: PlatformAuditLogEntry) -> None:
        row = PlatformAuditLogRow(
            id=entry.id,
            timestamp=datetime.fromisoformat(entry.timestamp),
            expires_at=datetime.fromisoformat(entry.expires_at),
            actor_user_id=entry.actor_user_id,
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            tenant_schema=entry.tenant_schema,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            details=entry.details,
        )
        self._session.add(row)
        self._session.flush()

    def recent(self, limit: int = 100) -> list[PlatformAuditLogEntry]:

        rows = (
            self._session.execute(
                select(PlatformAuditLogRow)
                .order_by(desc(PlatformAuditLogRow.timestamp))
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_row_to_entry(row) for row in rows]


def _row_to_entry(row: PlatformAuditLogRow) -> PlatformAuditLogEntry:
    from ...models.platform_audit import PlatformAuditLogEntry  # noqa: PLC0415

    return PlatformAuditLogEntry(
        id=row.id,
        timestamp=row.timestamp.isoformat().replace("+00:00", "Z"),
        expires_at=row.expires_at.isoformat().replace("+00:00", "Z"),
        actor_user_id=row.actor_user_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        tenant_schema=row.tenant_schema,
        ip_address=row.ip_address,
        user_agent=row.user_agent,
        details=row.details,
    )
