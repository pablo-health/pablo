# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL audit log repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import distinct, select

from ...db.models import AuditLogRow
from ..audit import DEFAULT_BASELINE_DAYS, AuditRepository, _assert_phi_free

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

    def metadata_for_review(
        self, window_hours: int = 24, baseline_days: int = DEFAULT_BASELINE_DAYS
    ) -> list[dict]:
        now = datetime.now(UTC)
        window_start = now - timedelta(hours=window_hours)
        baseline_start = now - timedelta(days=baseline_days)

        window_rows = (
            self._session.query(AuditLogRow)
            .filter(AuditLogRow.timestamp >= window_start)
            .order_by(AuditLogRow.timestamp.asc())
            .all()
        )

        # One query per dimension against the baseline window. Returns
        # only distinct tuples — cheap, and none of it is PHI.
        known_user_patient = set(
            self._session.execute(
                select(distinct(AuditLogRow.user_id), AuditLogRow.patient_id).where(
                    AuditLogRow.timestamp >= baseline_start,
                    AuditLogRow.timestamp < window_start,
                    AuditLogRow.patient_id.is_not(None),
                )
            ).all()
        )
        known_user_ip = set(
            self._session.execute(
                select(distinct(AuditLogRow.user_id), AuditLogRow.ip_address).where(
                    AuditLogRow.timestamp >= baseline_start,
                    AuditLogRow.timestamp < window_start,
                    AuditLogRow.ip_address.is_not(None),
                )
            ).all()
        )
        known_user_agent = set(
            self._session.execute(
                select(distinct(AuditLogRow.user_id), AuditLogRow.user_agent).where(
                    AuditLogRow.timestamp >= baseline_start,
                    AuditLogRow.timestamp < window_start,
                    AuditLogRow.user_agent.is_not(None),
                )
            ).all()
        )

        out = []
        for row in window_rows:
            entry = _row_to_dict(row)
            entry["is_novel_user_patient"] = bool(
                row.patient_id and (row.user_id, row.patient_id) not in known_user_patient
            )
            entry["is_novel_user_ip"] = bool(
                row.ip_address and (row.user_id, row.ip_address) not in known_user_ip
            )
            entry["is_novel_user_agent"] = bool(
                row.user_agent and (row.user_id, row.user_agent) not in known_user_agent
            )
            out.append(entry)

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
