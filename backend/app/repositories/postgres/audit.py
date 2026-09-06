# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL audit log repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import distinct, func, select

from ...db.models import AuditLogRow
from ...models.audit import ACTOR_TYPE_PATIENT
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

    def earliest_create_for_patients(self, patient_ids: set[str]) -> dict[str, datetime | None]:
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

    def list_for_user(
        self,
        user_id: str,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditLogEntry]:
        from ...models.audit import AuditLogEntry  # noqa: PLC0415

        query = select(AuditLogRow).where(AuditLogRow.user_id == user_id)
        if since is not None:
            query = query.where(AuditLogRow.timestamp > since)
        query = query.order_by(AuditLogRow.timestamp.desc()).limit(limit)

        rows = self._session.execute(query).scalars().all()
        return [
            AuditLogEntry(
                id=row.id,
                timestamp=row.timestamp.isoformat().replace("+00:00", "Z"),
                expires_at=row.expires_at.isoformat().replace("+00:00", "Z"),
                user_id=row.user_id,
                actor_type=row.actor_type,
                actor_component=row.actor_component,
                action=row.action,
                resource_type=row.resource_type,
                resource_id=row.resource_id,
                patient_id=row.patient_id,
                session_id=row.session_id,
                ip_address=row.ip_address,
                user_agent=row.user_agent,
                changes=row.changes,
            )
            for row in rows
        ]

    def append(self, entry: AuditLogEntry) -> None:
        row = AuditLogRow(
            id=entry.id,
            timestamp=datetime.fromisoformat(entry.timestamp),
            expires_at=datetime.fromisoformat(entry.expires_at),
            user_id=entry.user_id,
            actor_type=entry.actor_type,
            actor_component=entry.actor_component,
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

        # Patient-actor rows are excluded from all three queries below.
        # This surface reviews CLINICIAN access — "who looked at a chart
        # they had no business in" — and it derives that from ``user_id``
        # alone, with no notion of what kind of principal that id names.
        # A patient acting on their own record writes a row whose
        # ``user_id`` is their own patient id, so without this predicate
        # the review treats the patient as a user who accessed a patient,
        # and a self-action pairs them with themselves: a pair no
        # clinician baseline has ever seen, which is exactly what
        # ``is_novel_user_patient`` means. A patient reading their own
        # chart would surface as novel clinician access.
        #
        # RLS keeps those rows out of a clinician's session today, so the
        # surface is correct in production for a reason unrelated to this
        # query being right. Excluding them here makes it true of the
        # query rather than of the caller's connection.
        not_patient_actor = AuditLogRow.actor_type != ACTOR_TYPE_PATIENT

        window_rows = (
            self._session.execute(
                select(AuditLogRow)
                .where(AuditLogRow.timestamp >= window_start, not_patient_actor)
                .order_by(AuditLogRow.timestamp.asc())
            )
            .scalars()
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
                .where(not_patient_actor)
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
                    not_patient_actor,
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
