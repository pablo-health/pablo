# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL audit log repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import distinct, func, select

from ...db.models import AuditLogRow
from ..audit import (
    DEFAULT_BASELINE_DAYS,
    MIN_USER_BASELINE_DAYS,
    AuditRepository,
    _assert_phi_free,
)

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

    def earliest_create_for_patients(
        self, patient_ids: set[str]
    ) -> dict[str, datetime | None]:
        out: dict[str, datetime | None] = dict.fromkeys(patient_ids)
        if not patient_ids:
            return out
        rows = self._session.execute(
            select(AuditLogRow.patient_id, func.min(AuditLogRow.timestamp))
            .where(
                AuditLogRow.action == "patient_created",
                AuditLogRow.patient_id.in_(patient_ids),
            )
            .group_by(AuditLogRow.patient_id)
        ).all()
        for patient_id, earliest in rows:
            out[patient_id] = earliest
        return out

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
        min_baseline_cutoff = now - timedelta(days=MIN_USER_BASELINE_DAYS)

        window_rows = (
            self._session.query(AuditLogRow)
            .filter(AuditLogRow.timestamp >= window_start)
            .order_by(AuditLogRow.timestamp.asc())
            .all()
        )

        # Users whose earliest audit activity predates MIN_USER_BASELINE_DAYS.
        # Only these users get novelty checks — protects first-week users,
        # returning-from-long-absence users, and brand-new installs from
        # spurious flags against a thin baseline.
        users_with_sufficient_baseline = {
            row[0]
            for row in self._session.execute(
                select(AuditLogRow.user_id)
                .group_by(AuditLogRow.user_id)
                .having(func.min(AuditLogRow.timestamp) < min_baseline_cutoff)
            ).all()
        }

        # Distinct (user, patient) pairs seen in the baseline window. None of
        # this is PHI; IDs only.
        known_user_patient = set(
            self._session.execute(
                select(distinct(AuditLogRow.user_id), AuditLogRow.patient_id).where(
                    AuditLogRow.timestamp >= baseline_start,
                    AuditLogRow.timestamp < window_start,
                    AuditLogRow.patient_id.is_not(None),
                )
            ).all()
        )

        # Same-window creates suppress novelty (user just made the patient).
        created_in_window = {
            (r.user_id, r.patient_id)
            for r in window_rows
            if r.action == "patient_created" and r.patient_id
        }

        out = []
        for row in window_rows:
            entry = _row_to_dict(row)
            entry["is_novel_user_patient"] = bool(
                row.patient_id
                and row.user_id in users_with_sufficient_baseline
                and (row.user_id, row.patient_id) not in known_user_patient
                and (row.user_id, row.patient_id) not in created_in_window
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
