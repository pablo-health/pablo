# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL therapy session repository implementation.

Access is gated by ``patient_clinicians`` grants on the session's
patient, not by the session's own ``user_id`` column. ``user_id`` on
``therapy_sessions`` stays — but as historical actor data ("who
recorded this session") rather than an access proxy. A primary
clinician with co-treating peers, supervisors, or coverage clinicians
now sees the full session history for their patient regardless of who
recorded each session; previously the ``user_id`` filter would have
hidden sessions recorded by anyone else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, func, or_, select, text

from ...db.models import PatientClinicianRow, TherapySessionRow
from ...models.session import TherapySession, Transcript
from ...utcnow import utc_now
from ..session import TherapySessionRepository, _compute_day_boundaries

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from sqlalchemy.orm import Session


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _grant_filters(user_id: str) -> tuple:
    """Predicates for "user has a non-expired grant on the joined row's patient_id"."""
    return (
        PatientClinicianRow.user_id == user_id,
        or_(
            PatientClinicianRow.expires_at.is_(None),
            PatientClinicianRow.expires_at > utc_now(),
        ),
    )


class PostgresTherapySessionRepository(TherapySessionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, session_id: str, user_id: str) -> TherapySession | None:
        row = self._session.execute(
            select(TherapySessionRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
            )
            .where(
                TherapySessionRow.id == session_id,
                TherapySessionRow.deleted_at.is_(None),
                *_grant_filters(user_id),
            )
        ).scalar_one_or_none()
        return _row_to_session(row) if row else None

    def list_by_patient(self, patient_id: str, user_id: str) -> list[TherapySession]:
        rows = (
            self._session.execute(
                select(TherapySessionRow)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
                )
                .where(
                    TherapySessionRow.patient_id == patient_id,
                    TherapySessionRow.deleted_at.is_(None),
                    *_grant_filters(user_id),
                )
                .order_by(TherapySessionRow.session_date.desc())
            )
            .scalars()
            .all()
        )
        return [_row_to_session(r) for r in rows]

    def session_dates_by_patient(self, patient_id: str, user_id: str) -> list[datetime]:
        """Timestamp-only variant of :meth:`list_by_patient` — never selects
        transcript or note content, so it stays valid under column-scoped
        grants."""
        rows = (
            self._session.execute(
                select(TherapySessionRow.session_date)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
                )
                .where(
                    TherapySessionRow.patient_id == patient_id,
                    TherapySessionRow.deleted_at.is_(None),
                    *_grant_filters(user_id),
                )
                .order_by(TherapySessionRow.session_date.desc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_by_user(
        self, user_id: str, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[TherapySession], int]:
        """List sessions for any patient ``user_id`` has access to.

        Semantic note: this method historically returned "sessions whose
        ``user_id`` matches" — i.e. sessions the caller personally
        recorded. Post patient_clinicians, it returns sessions for
        patients the caller has any grant on (primary, co-treating,
        supervisor, or coverage). For single-clinician practices the
        result is unchanged; for multi-clinician practices the caller
        now sees the full chart history they're authorized to see.
        """
        base = (
            select(TherapySessionRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
            )
            .where(
                TherapySessionRow.deleted_at.is_(None),
                *_grant_filters(user_id),
            )
        )
        total = self._session.scalar(select(func.count()).select_from(base.subquery())) or 0
        offset = (page - 1) * page_size
        rows = (
            self._session.execute(
                base.order_by(TherapySessionRow.session_date.desc()).offset(offset).limit(page_size)
            )
            .scalars()
            .all()
        )
        return [_row_to_session(r) for r in rows], total

    def create(self, session: TherapySession) -> TherapySession:
        """Insert a session row.

        ``session.user_id`` records who is creating this session (actor /
        owner-of-record). No access check at create time — the calling
        service has already verified ``user_id`` has access to
        ``patient_id`` via ``patient_repo.get(patient_id, user_id)``.
        """
        row = TherapySessionRow()
        _session_to_row(session, row)
        self._session.add(row)
        self._session.flush()
        return session

    def update(self, session: TherapySession) -> TherapySession:
        row = self._session.get(TherapySessionRow, session.id)
        if row is None:
            row = TherapySessionRow()
            self._session.add(row)
        _session_to_row(session, row)
        self._session.flush()
        return session

    def list_today_by_user(self, user_id: str, tz_name: str = "UTC") -> list[TherapySession]:
        """List today's scheduled sessions for any patient the user has access to."""
        start_utc, end_utc = _compute_day_boundaries(tz_name)
        rows = (
            self._session.execute(
                select(TherapySessionRow)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
                )
                .where(
                    TherapySessionRow.scheduled_at.is_not(None),
                    TherapySessionRow.scheduled_at >= start_utc,
                    TherapySessionRow.scheduled_at < end_utc,
                    TherapySessionRow.deleted_at.is_(None),
                    *_grant_filters(user_id),
                )
                .order_by(TherapySessionRow.scheduled_at)
            )
            .scalars()
            .all()
        )
        return [_row_to_session(r) for r in rows]

    def count_by_status(self, user_id: str) -> dict[str, int]:
        rows = self._session.execute(
            select(TherapySessionRow.status, func.count())
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
            )
            .where(
                TherapySessionRow.deleted_at.is_(None),
                *_grant_filters(user_id),
            )
            .group_by(TherapySessionRow.status)
        ).all()
        return {str(status): count for status, count in rows}

    def list_recent_by_status(
        self, user_id: str, status: str, *, limit: int
    ) -> list[TherapySession]:
        rows = (
            self._session.execute(
                select(TherapySessionRow)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
                )
                .where(
                    TherapySessionRow.status == status,
                    TherapySessionRow.deleted_at.is_(None),
                    *_grant_filters(user_id),
                )
                .order_by(TherapySessionRow.session_date.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_row_to_session(r) for r in rows]

    def get_next_session_date(
        self,
        patient_id: str,
        user_id: str,
        *,
        after: datetime,
        exclude_statuses: Collection[str],
    ) -> datetime | None:
        effective_date = func.coalesce(
            TherapySessionRow.scheduled_at, TherapySessionRow.session_date
        )
        query = (
            select(func.min(effective_date))
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
            )
            .where(
                TherapySessionRow.patient_id == patient_id,
                TherapySessionRow.deleted_at.is_(None),
                effective_date > after,
                *_grant_filters(user_id),
            )
        )
        if exclude_statuses:
            query = query.where(TherapySessionRow.status.notin_(list(exclude_statuses)))
        next_date: datetime | None = self._session.scalar(query)
        return next_date

    def get_session_number_for_patient(self, patient_id: str) -> int:
        # Numbering is monotonic — count soft-deleted sessions too so a
        # restored / re-listed patient doesn't collide on session_number.
        result = self._session.scalar(
            select(func.max(TherapySessionRow.session_number)).where(
                TherapySessionRow.patient_id == patient_id
            )
        )
        return (result or 0) + 1

    def delete(self, session_id: str, user_id: str) -> bool:
        """Soft-delete a single therapy session (THERAPY-nyb).

        No HTTP route currently exposes this — it exists so the cascade
        from ``PatientRepository.delete`` and the future per-session
        delete UI (THERAPY-yg2) share one code path.
        """
        row = self._session.get(TherapySessionRow, session_id)
        if row is None or row.deleted_at is not None:
            return False
        if not self._has_patient_access(row.patient_id, user_id):
            return False
        row.deleted_at = utc_now()
        self._session.flush()
        return True

    def _physical_delete(self, session_id: str) -> bool:
        """Internal — purge cron only (THERAPY-cgy). Not HTTP-exposed.

        Bypasses the access check by design: the cron runs as a system
        identity that legitimately has no per-patient grant.
        """
        row = self._session.get(TherapySessionRow, session_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    def _has_patient_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL,
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)


def _row_to_session(row: TherapySessionRow) -> TherapySession:
    transcript = Transcript(
        format=row.transcript["format"],
        content=row.transcript["content"],
    )
    return TherapySession(
        id=row.id,
        user_id=row.user_id,
        patient_id=row.patient_id,
        session_date=row.session_date,
        session_number=row.session_number,
        status=row.status,
        transcript=transcript,
        created_at=row.created_at,
        scheduled_at=row.scheduled_at,
        video_link=row.video_link,
        video_platform=row.video_platform,
        session_type=row.session_type,
        duration_minutes=row.duration_minutes,
        source=row.source,
        notes=row.notes,
        started_at=row.started_at,
        ended_at=row.ended_at,
        updated_at=row.updated_at,
        audio_gcs_path=row.audio_gcs_path,
        transcription_job_metadata=row.transcription_job_metadata,
        processing_started_at=row.processing_started_at,
        processing_completed_at=row.processing_completed_at,
        error=row.error,
        redacted_transcript=row.redacted_transcript,
        naturalized_transcript=row.naturalized_transcript,
    )


def _session_to_row(session: TherapySession, row: TherapySessionRow) -> None:
    row.id = session.id
    row.user_id = session.user_id
    row.patient_id = session.patient_id
    row.session_date = session.session_date
    row.session_number = session.session_number
    row.status = session.status
    row.transcript = session.transcript.to_dict()
    row.created_at = session.created_at
    row.scheduled_at = session.scheduled_at
    row.video_link = session.video_link
    row.video_platform = session.video_platform
    row.session_type = session.session_type
    row.duration_minutes = session.duration_minutes
    row.source = session.source
    row.notes = session.notes
    row.started_at = session.started_at
    row.ended_at = session.ended_at
    row.updated_at = session.updated_at
    row.audio_gcs_path = session.audio_gcs_path
    row.transcription_job_metadata = session.transcription_job_metadata
    row.processing_started_at = session.processing_started_at
    row.processing_completed_at = session.processing_completed_at
    row.error = session.error
    row.redacted_transcript = session.redacted_transcript
    row.naturalized_transcript = session.naturalized_transcript
