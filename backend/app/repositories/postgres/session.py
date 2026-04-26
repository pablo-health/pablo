# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL therapy session repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func

from ...db.models import TherapySessionRow
from ...models.session import TherapySession, Transcript
from ..session import TherapySessionRepository, _compute_day_boundaries

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresTherapySessionRepository(TherapySessionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, session_id: str, user_id: str) -> TherapySession | None:
        row = self._session.get(TherapySessionRow, session_id)
        if row is None or row.user_id != user_id:
            return None
        return _row_to_session(row)

    def list_by_patient(self, patient_id: str, user_id: str) -> list[TherapySession]:
        rows = (
            self._session.query(TherapySessionRow)
            .filter(
                TherapySessionRow.patient_id == patient_id,
                TherapySessionRow.user_id == user_id,
            )
            .order_by(TherapySessionRow.session_date.desc())
            .all()
        )
        return [_row_to_session(r) for r in rows]

    def list_by_user(
        self, user_id: str, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[TherapySession], int]:
        base = self._session.query(TherapySessionRow).filter(TherapySessionRow.user_id == user_id)
        total = base.count()
        offset = (page - 1) * page_size
        rows = (
            base.order_by(TherapySessionRow.session_date.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
        return [_row_to_session(r) for r in rows], total

    def create(self, session: TherapySession) -> TherapySession:
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
        start_utc, end_utc = _compute_day_boundaries(tz_name)
        rows = (
            self._session.query(TherapySessionRow)
            .filter(
                TherapySessionRow.user_id == user_id,
                TherapySessionRow.scheduled_at.is_not(None),
                TherapySessionRow.scheduled_at >= start_utc,
                TherapySessionRow.scheduled_at < end_utc,
            )
            .order_by(TherapySessionRow.scheduled_at)
            .all()
        )
        return [_row_to_session(r) for r in rows]

    def get_session_number_for_patient(self, patient_id: str) -> int:
        result = (
            self._session.query(func.max(TherapySessionRow.session_number))
            .filter(TherapySessionRow.patient_id == patient_id)
            .scalar()
        )
        return (result or 0) + 1


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
