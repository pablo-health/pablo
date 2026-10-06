# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL session dictation repository.

The table's row policy is the note-child policy, so a dictation is visible
exactly when its note is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from ...db.models import SessionDictationRow
from ...models.session_dictation import DictationStatus, DictationUse, SessionDictation
from ..session_dictation import SessionDictationRepository

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


def _to_dictation(row: SessionDictationRow) -> SessionDictation:
    return SessionDictation(
        id=str(row.id),
        session_id=str(row.session_id),
        note_id=str(row.note_id),
        patient_id=str(row.patient_id),
        author_user_id=str(row.author_user_id),
        audio_path=row.audio_path,
        content_type=row.content_type,
        status=cast("DictationStatus", row.status),
        created_at=row.created_at,
        duration_seconds=row.duration_seconds,
        transcript=row.transcript,
        used_as=cast("DictationUse | None", row.used_as),
        addendum_id=str(row.addendum_id) if row.addendum_id else None,
        transcribed_at=row.transcribed_at,
    )


class PostgresSessionDictationRepository(SessionDictationRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _row(self, dictation_id: str) -> SessionDictationRow:
        row = self._session.get(SessionDictationRow, dictation_id)
        if row is None:
            raise KeyError(dictation_id)
        return row

    def add(self, dictation: SessionDictation) -> SessionDictation:
        self._session.add(
            SessionDictationRow(
                id=dictation.id,
                session_id=dictation.session_id,
                note_id=dictation.note_id,
                patient_id=dictation.patient_id,
                author_user_id=dictation.author_user_id,
                audio_path=dictation.audio_path,
                content_type=dictation.content_type,
                duration_seconds=dictation.duration_seconds,
                status=dictation.status,
                created_at=dictation.created_at,
            )
        )
        self._session.flush()
        return dictation

    def get(self, dictation_id: str) -> SessionDictation | None:
        row = self._session.get(SessionDictationRow, dictation_id)
        return _to_dictation(row) if row else None

    def list_for_session(self, session_id: str) -> list[SessionDictation]:
        rows = self._session.scalars(
            select(SessionDictationRow)
            .where(SessionDictationRow.session_id == session_id)
            .order_by(SessionDictationRow.created_at, SessionDictationRow.id)
        ).all()
        return [_to_dictation(row) for row in rows]

    def record_transcript(
        self,
        dictation_id: str,
        *,
        status: DictationStatus,
        transcript: str | None,
        used_as: DictationUse | None,
        transcribed_at: datetime,
    ) -> SessionDictation:
        row = self._row(dictation_id)
        row.status = status
        row.transcript = transcript
        row.used_as = used_as
        row.transcribed_at = transcribed_at
        self._session.flush()
        return _to_dictation(row)

    def link_addendum(self, dictation_id: str, addendum_id: str) -> SessionDictation:
        row = self._row(dictation_id)
        row.addendum_id = addendum_id
        self._session.flush()
        return _to_dictation(row)
