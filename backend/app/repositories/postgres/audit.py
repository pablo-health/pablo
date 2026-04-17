# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL audit log repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ...db.models import AuditLogRow
from ..audit import AuditRepository, _assert_phi_free

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ...models.audit import AuditLogEntry


class PostgresAuditRepository(AuditRepository):
    """Postgres-backed audit log repository.

    Writes through `append()`; never mutates or deletes. Row lifecycle is
    handled by a separate retention job that deletes rows where
    expires_at < now() (7y retention per AUDIT_LOG_RETENTION_DAYS).
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, entry: AuditLogEntry) -> None:
        row = AuditLogRow(
            id=entry.id,
            timestamp=_parse_iso(entry.timestamp),
            expires_at=_parse_iso(entry.expires_at),
            user_id=entry.user_id,
            action=entry.action,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            patient_id=entry.patient_id,
            session_id=entry.session_id,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            changes=entry.changes,
        )
        self._session.add(row)
        self._session.flush()

    def metadata_for_review(self, window_hours: int = 24) -> list[dict]:
        cutoff = datetime.now(UTC) - timedelta(hours=window_hours)
        rows = (
            self._session.query(AuditLogRow)
            .filter(AuditLogRow.timestamp >= cutoff)
            .order_by(AuditLogRow.timestamp.asc())
            .all()
        )
        out = [_row_to_dict(r) for r in rows]
        _assert_phi_free(out)
        return out


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _row_to_dict(row: AuditLogRow) -> dict:
    return {
        "id": row.id,
        "timestamp": row.timestamp.isoformat().replace("+00:00", "Z"),
        "user_id": row.user_id,
        "action": row.action,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "patient_id": row.patient_id,
        "session_id": row.session_id,
        "ip_address": row.ip_address,
        "user_agent": row.user_agent,
        "changes": row.changes,
    }
