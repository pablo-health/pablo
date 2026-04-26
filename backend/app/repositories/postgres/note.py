# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL NotesRepository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import NoteRow
from ...models.note import Note
from ..note import NotesRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresNotesRepository(NotesRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, note_id: str) -> Note | None:
        row = self._session.get(NoteRow, note_id)
        return _row_to_note(row) if row else None

    def get_by_session_id(self, session_id: str) -> Note | None:
        row = (
            self._session.query(NoteRow)
            .filter(NoteRow.session_id == session_id)
            .one_or_none()
        )
        return _row_to_note(row) if row else None

    def list_by_patient(self, patient_id: str) -> list[Note]:
        rows = (
            self._session.query(NoteRow)
            .filter(NoteRow.patient_id == patient_id)
            .order_by(
                NoteRow.finalized_at.desc().nullslast(),
                NoteRow.created_at.desc(),
            )
            .all()
        )
        return [_row_to_note(r) for r in rows]

    def add(self, note: Note) -> Note:
        row = NoteRow()
        _note_to_row(note, row)
        self._session.add(row)
        self._session.flush()
        return note

    def update(self, note: Note) -> Note:
        row = self._session.get(NoteRow, note.id)
        if row is None:
            row = NoteRow()
            self._session.add(row)
        _note_to_row(note, row)
        self._session.flush()
        return note

    def delete(self, note_id: str) -> None:
        row = self._session.get(NoteRow, note_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()


def _row_to_note(row: NoteRow) -> Note:
    return Note(
        id=row.id,
        patient_id=row.patient_id,
        session_id=row.session_id,
        note_type=row.note_type,
        content=row.content,
        content_edited=row.content_edited,
        finalized_at=row.finalized_at,
        quality_rating=row.quality_rating,
        quality_rating_reason=row.quality_rating_reason,
        quality_rating_sections=row.quality_rating_sections,
        export_status=row.export_status,
        export_queued_at=row.export_queued_at,
        export_reviewed_at=row.export_reviewed_at,
        export_reviewed_by=row.export_reviewed_by,
        exported_at=row.exported_at,
        redacted_content=row.redacted_content,
        naturalized_content=row.naturalized_content,
        redacted_export_payload=row.redacted_export_payload,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _note_to_row(note: Note, row: NoteRow) -> None:
    row.id = note.id
    row.patient_id = note.patient_id
    row.session_id = note.session_id
    row.note_type = note.note_type
    row.content = note.content
    row.content_edited = note.content_edited
    row.finalized_at = note.finalized_at
    row.quality_rating = note.quality_rating
    row.quality_rating_reason = note.quality_rating_reason
    row.quality_rating_sections = note.quality_rating_sections
    row.export_status = note.export_status
    row.export_queued_at = note.export_queued_at
    row.export_reviewed_at = note.export_reviewed_at
    row.export_reviewed_by = note.export_reviewed_by
    row.exported_at = note.exported_at
    row.redacted_content = note.redacted_content
    row.naturalized_content = note.naturalized_content
    row.redacted_export_payload = note.redacted_export_payload
    row.created_at = note.created_at
    row.updated_at = note.updated_at
