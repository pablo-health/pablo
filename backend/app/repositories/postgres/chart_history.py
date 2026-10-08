# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL chart-history repository.

Runs inside the request's tenant-scoped session, so the row policy
(``has_patient_access``) applies to every statement here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from ...chart_history.models import HistoryEntry, HistoryRevision
from ...db.models import (
    NoteRow,
    PatientChartHistoryRevisionRow,
    PatientChartHistoryRow,
    TherapySessionRow,
)
from ..chart_history import ChartHistoryRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresChartHistoryRepository(ChartHistoryRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def entries(self, patient_id: str) -> list[HistoryEntry]:
        # The source note's date is its visit's, or when the note was written
        # for one with no session. A note the reader may not open has none.
        note_date = func.coalesce(TherapySessionRow.session_date, NoteRow.created_at)
        rows = self._session.execute(
            select(PatientChartHistoryRow, note_date)
            .outerjoin(NoteRow, NoteRow.id == PatientChartHistoryRow.source_note_id)
            .outerjoin(TherapySessionRow, TherapySessionRow.id == NoteRow.session_id)
            .where(PatientChartHistoryRow.patient_id == patient_id)
        ).all()
        return [
            HistoryEntry(
                id=row.id,
                patient_id=row.patient_id,
                field_key=row.field_key,
                text=row.text,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
                source_note_id=row.source_note_id,
                source_note_date=dated,
            )
            for row, dated in rows
        ]

    def revisions(self, patient_id: str) -> list[HistoryRevision]:
        rows = self._session.scalars(
            select(PatientChartHistoryRevisionRow)
            .where(PatientChartHistoryRevisionRow.patient_id == patient_id)
            .order_by(PatientChartHistoryRevisionRow.replaced_at.desc())
        ).all()
        return [
            HistoryRevision(
                id=r.id,
                patient_id=r.patient_id,
                field_key=r.field_key,
                text=r.text,
                written_at=r.written_at,
                written_by=r.written_by,
                replaced_at=r.replaced_at,
                replaced_by=r.replaced_by,
                source_note_id=r.source_note_id,
            )
            for r in rows
        ]

    def write(self, entry: HistoryEntry, replaced: HistoryRevision | None) -> None:
        if replaced is not None:
            self._session.add(
                PatientChartHistoryRevisionRow(
                    id=replaced.id,
                    patient_id=replaced.patient_id,
                    field_key=replaced.field_key,
                    text=replaced.text,
                    source_note_id=replaced.source_note_id,
                    written_by=replaced.written_by,
                    written_at=replaced.written_at,
                    replaced_by=replaced.replaced_by,
                    replaced_at=replaced.replaced_at,
                )
            )
        row = self._session.get(PatientChartHistoryRow, entry.id)
        if row is None:
            row = PatientChartHistoryRow(
                id=entry.id, patient_id=entry.patient_id, field_key=entry.field_key
            )
            self._session.add(row)
        row.text = entry.text
        row.source_note_id = entry.source_note_id
        row.updated_by = entry.updated_by
        row.updated_at = entry.updated_at
        self._session.flush()
