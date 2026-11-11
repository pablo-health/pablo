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

import uuid
from datetime import timedelta
from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, func, select, text, update

from ...db.models import NoteRow, PatientClinicianRow, TherapySessionRow
from ...models.session import TherapySession, Transcript
from ...utcnow import utc_now
from ..session import TherapySessionRepository, _compute_day_boundaries
from ._grants import live_grant

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from sqlalchemy.orm import Session


def _is_uuid(value: str) -> bool:
    """True iff ``value`` is a syntactically valid UUID string.

    A path segment that is not one means "no such session"; without the
    guard the uuid column comparison raises a DataError (a 500).
    """
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
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
                *live_grant(user_id),
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
                    *live_grant(user_id),
                )
                .order_by(TherapySessionRow.session_date.desc())
            )
            .scalars()
            .all()
        )
        return [_row_to_session(r) for r in rows]

    def get_multiple(self, session_ids: list[str], user_id: str) -> dict[str, TherapySession]:
        if not session_ids:
            return {}
        rows = (
            self._session.execute(
                select(TherapySessionRow)
                .join(
                    PatientClinicianRow,
                    PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
                )
                .where(
                    TherapySessionRow.id.in_(session_ids),
                    TherapySessionRow.deleted_at.is_(None),
                    *live_grant(user_id),
                )
            )
            .scalars()
            .all()
        )
        return {row.id: _row_to_session(row) for row in rows}

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
                    *live_grant(user_id),
                )
                .order_by(TherapySessionRow.session_date.desc())
            )
            .scalars()
            .all()
        )
        return list(rows)

    def list_by_user(
        self,
        user_id: str,
        *,
        page: int = 1,
        page_size: int = 20,
        patient_id: str | None = None,
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
        if patient_id is not None and not _is_uuid(patient_id):
            return [], 0
        base = (
            select(TherapySessionRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
            )
            .where(
                TherapySessionRow.deleted_at.is_(None),
                *live_grant(user_id),
            )
        )
        if patient_id is not None:
            base = base.where(TherapySessionRow.patient_id == patient_id)
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
                    *live_grant(user_id),
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
                *live_grant(user_id),
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
                    *live_grant(user_id),
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
                *live_grant(user_id),
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
        """Soft-delete a single therapy session and its note (THERAPY-nyb).

        No HTTP route currently exposes this — it exists so the future
        per-session delete UI (THERAPY-yg2) and :meth:`restore` share one
        stamp: the note is tombstoned with the session's own ``deleted_at``,
        which is how ``restore`` tells it apart from a note a later patient
        delete cascaded onto.
        """
        if not _is_uuid(session_id):
            return False
        row = self._session.get(TherapySessionRow, session_id)
        if row is None or row.deleted_at is not None:
            return False
        if not self._has_patient_access(row.patient_id, user_id):
            return False
        now = utc_now()
        self._session.execute(
            update(NoteRow)
            .where(NoteRow.session_id == session_id, NoteRow.deleted_at.is_(None))
            .values({NoteRow.deleted_at: now})
            .execution_options(synchronize_session=False)
        )
        row.deleted_at = now
        self._session.flush()
        return True

    def get_recently_deleted(
        self, session_id: str, user_id: str, *, window_days: int = 30
    ) -> tuple[TherapySession, datetime] | None:
        """One soft-deleted session inside the undo window, or ``None``."""
        row = self._recently_deleted_row(session_id, user_id, window_days=window_days)
        if row is None or row.deleted_at is None:
            return None
        return _row_to_session(row), row.deleted_at

    def list_recently_deleted(
        self,
        user_id: str,
        *,
        patient_id: str | None = None,
        window_days: int = 30,
    ) -> list[tuple[TherapySession, datetime]]:
        """Soft-deleted sessions still inside the undo window, newest first."""
        if patient_id is not None and not _is_uuid(patient_id):
            return []
        cutoff = utc_now() - timedelta(days=window_days)
        query = (
            select(TherapySessionRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == TherapySessionRow.patient_id,
            )
            .where(
                TherapySessionRow.deleted_at.isnot(None),
                TherapySessionRow.deleted_at > cutoff,
                *live_grant(user_id),
            )
        )
        if patient_id is not None:
            query = query.where(TherapySessionRow.patient_id == patient_id)
        rows = (
            self._session.execute(query.order_by(TherapySessionRow.session_date.desc()))
            .scalars()
            .all()
        )
        return [(_row_to_session(r), r.deleted_at) for r in rows if r.deleted_at is not None]

    def restore(
        self, session_id: str, user_id: str, *, window_days: int = 30
    ) -> TherapySession | None:
        """Reverse :meth:`delete` by clearing ``deleted_at`` (PABLO-ocvu)."""
        row = self._recently_deleted_row(session_id, user_id, window_days=window_days)
        if row is None:
            return None
        # Only the note stamped together with the session comes back. A note
        # tombstoned on its own earlier, or by a patient delete after this
        # session was already gone, carries a different stamp and stays put.
        self._session.execute(
            update(NoteRow)
            .where(NoteRow.session_id == session_id, NoteRow.deleted_at == row.deleted_at)
            .values({NoteRow.deleted_at: None})
            .execution_options(synchronize_session=False)
        )
        row.deleted_at = None
        self._session.flush()
        return _row_to_session(row)

    def _recently_deleted_row(
        self, session_id: str, user_id: str, *, window_days: int
    ) -> TherapySessionRow | None:
        """The tombstoned row behind ``get_recently_deleted`` / ``restore``.

        Absent, live, past-window and inaccessible all answer ``None`` so
        the caller cannot tell the lifecycle state of a row it may not act
        on — the same shape as :meth:`get`.
        """
        if not _is_uuid(session_id):
            return None
        row = self._session.get(TherapySessionRow, session_id)
        if row is None or row.deleted_at is None:
            return None
        if not self._has_patient_access(row.patient_id, user_id):
            return None
        if row.deleted_at <= utc_now() - timedelta(days=window_days):
            return None
        return row

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
        client_present_end_seconds=row.client_present_end_seconds,
        updated_at=row.updated_at,
        audio_gcs_path=row.audio_gcs_path,
        transcription_job_metadata=row.transcription_job_metadata,
        processing_started_at=row.processing_started_at,
        processing_completed_at=row.processing_completed_at,
        error=row.error,
        redacted_transcript=row.redacted_transcript,
        naturalized_transcript=row.naturalized_transcript,
        source_signer=row.source_signer,
        source_document_id=row.source_document_id,
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
    row.client_present_end_seconds = session.client_present_end_seconds
    row.updated_at = session.updated_at
    row.audio_gcs_path = session.audio_gcs_path
    row.transcription_job_metadata = session.transcription_job_metadata
    row.processing_started_at = session.processing_started_at
    row.processing_completed_at = session.processing_completed_at
    row.error = session.error
    row.redacted_transcript = session.redacted_transcript
    row.naturalized_transcript = session.naturalized_transcript
    row.source_signer = session.source_signer
    row.source_document_id = session.source_document_id
