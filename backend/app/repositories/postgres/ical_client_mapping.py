# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL iCal client mapping repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import ICalClientMappingRow
from ...utcnow import utc_now_iso
from ..ical_client_mapping import ICalClientMapping

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresICalClientMappingRepository:
    """PostgreSQL implementation — same interface as the Firestore version."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def get(
        self, user_id: str, ehr_system: str, client_identifier: str
    ) -> ICalClientMapping | None:
        doc_id = f"{user_id}_{ehr_system}_{client_identifier}"
        row = self._session.get(ICalClientMappingRow, doc_id)
        if row is None:
            return None
        return _row_to_mapping(row)

    def list_by_user(self, user_id: str) -> list[ICalClientMapping]:
        rows = (
            self._session.query(ICalClientMappingRow)
            .filter(ICalClientMappingRow.user_id == user_id)
            .all()
        )
        return [_row_to_mapping(r) for r in rows]

    def list_by_source(self, user_id: str, ehr_system: str) -> list[ICalClientMapping]:
        rows = (
            self._session.query(ICalClientMappingRow)
            .filter(
                ICalClientMappingRow.user_id == user_id,
                ICalClientMappingRow.ehr_system == ehr_system,
            )
            .all()
        )
        return [_row_to_mapping(r) for r in rows]

    def save(self, mapping: ICalClientMapping) -> None:
        if not mapping.created_at:
            mapping.created_at = utc_now_iso()
        row = self._session.get(ICalClientMappingRow, mapping.doc_id)
        if row is None:
            row = ICalClientMappingRow(doc_id=mapping.doc_id)
            self._session.add(row)
        row.user_id = mapping.user_id
        row.ehr_system = mapping.ehr_system
        row.client_identifier = mapping.client_identifier
        row.patient_id = mapping.patient_id
        row.created_at = mapping.created_at
        self._session.flush()

    def delete(self, user_id: str, ehr_system: str, client_identifier: str) -> bool:
        doc_id = f"{user_id}_{ehr_system}_{client_identifier}"
        row = self._session.get(ICalClientMappingRow, doc_id)
        if row is None:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_mapping(row: ICalClientMappingRow) -> ICalClientMapping:
    return ICalClientMapping(
        user_id=row.user_id,
        ehr_system=row.ehr_system,
        client_identifier=row.client_identifier,
        patient_id=row.patient_id,
        created_at=row.created_at,
    )
