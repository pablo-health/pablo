# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL patient source mapping repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, select

from ...db.models import PatientSourceMappingRow
from ...utcnow import utc_now
from ..patient_source_mapping import PatientSourceMapping, PatientSourceMappingRepository

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult
    from sqlalchemy.orm import Session


class PostgresPatientSourceMappingRepository(PatientSourceMappingRepository):
    """PostgreSQL implementation of PatientSourceMappingRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, user_id: str, source: str, source_identifier: str) -> PatientSourceMapping | None:
        row = self._session.get(PatientSourceMappingRow, f"{user_id}_{source}_{source_identifier}")
        if row is None:
            return None
        return _row_to_mapping(row)

    def list_by_source(self, user_id: str, source: str) -> list[PatientSourceMapping]:
        rows = (
            self._session.execute(
                select(PatientSourceMappingRow).where(
                    PatientSourceMappingRow.user_id == user_id,
                    PatientSourceMappingRow.source == source,
                )
            )
            .scalars()
            .all()
        )
        return [_row_to_mapping(r) for r in rows]

    def save(self, mapping: PatientSourceMapping) -> None:
        if not mapping.created_at:
            mapping.created_at = utc_now()
        row = self._session.get(PatientSourceMappingRow, mapping.doc_id)
        if row is None:
            row = PatientSourceMappingRow(doc_id=mapping.doc_id)
            self._session.add(row)
        row.user_id = mapping.user_id
        row.source = mapping.source
        row.source_identifier = mapping.source_identifier
        row.answer = mapping.answer
        row.patient_id = mapping.patient_id
        row.answered_title = mapping.answered_title
        row.created_at = mapping.created_at
        self._session.flush()

    def delete_by_source(self, user_id: str, source: str) -> int:
        # cast: Session.execute is typed Result[Any]; a DELETE returns a
        # CursorResult, which is what carries rowcount (as appointment.py).
        result = cast(
            "CursorResult[Any]",
            self._session.execute(
                delete(PatientSourceMappingRow).where(
                    PatientSourceMappingRow.user_id == user_id,
                    PatientSourceMappingRow.source == source,
                )
            ),
        )
        self._session.flush()
        return result.rowcount or 0


def _row_to_mapping(row: PatientSourceMappingRow) -> PatientSourceMapping:
    return PatientSourceMapping(
        user_id=row.user_id,
        source=row.source,
        source_identifier=row.source_identifier,
        patient_id=row.patient_id,
        created_at=row.created_at,
        answer=row.answer,
        answered_title=row.answered_title,
    )
