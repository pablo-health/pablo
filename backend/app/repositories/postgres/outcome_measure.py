# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL OutcomeMeasureRepository implementation.

Access checking mirrors the notes repository: every method delegates the
per-patient access predicate to the schema-local ``has_patient_access``
SQL function (migration ``777b846ab944``), which reads ``patient_clinicians``
and short-circuits at the DB layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, select, text

from ...db.models import OutcomeMeasureRow, PatientClinicianRow
from ..outcome_measure import OutcomeMeasureRepository, PatientOutcomeAccessDeniedError
from ._grants import live_grant

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..row import Row


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _row_to_dict(row: OutcomeMeasureRow) -> Row:
    return {
        "id": row.id,
        "patient_id": row.patient_id,
        "session_id": row.session_id,
        "appointment_id": row.appointment_id,
        "instrument": row.instrument,
        "total_score": row.total_score,
        "item_scores": row.item_scores,
        "is_complete": row.is_complete,
        "source": row.source,
        "item_citations": row.item_citations,
        "administered_at": row.administered_at,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
    }


class PostgresOutcomeMeasureRepository(OutcomeMeasureRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- internal access predicate ---

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL,
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    # --- reads ---

    def get(self, measure_id: str, user_id: str) -> Row | None:
        """Fetch by id with access check, or ``None`` if absent/denied."""
        row = self._session.execute(
            select(OutcomeMeasureRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == OutcomeMeasureRow.patient_id,
            )
            .where(
                OutcomeMeasureRow.id == measure_id,
                *live_grant(user_id),
            )
        ).scalar_one_or_none()
        return _row_to_dict(row) if row else None

    def list_by_patient(
        self,
        patient_id: str,
        user_id: str,
        *,
        instrument: str | None = None,
    ) -> list[Row]:
        if not self._has_access(patient_id, user_id):
            return []
        query = select(OutcomeMeasureRow).where(
            OutcomeMeasureRow.patient_id == patient_id,
        )
        if instrument is not None:
            query = query.where(OutcomeMeasureRow.instrument == instrument)
        query = query.order_by(OutcomeMeasureRow.administered_at.asc())
        return [_row_to_dict(r) for r in self._session.execute(query).scalars().all()]

    # --- writes ---

    def _insert(self, row: Row) -> Row:
        orm_row = OutcomeMeasureRow(
            id=str(row["id"]),
            patient_id=str(row["patient_id"]),
            session_id=row.get("session_id"),
            appointment_id=row.get("appointment_id"),
            instrument=str(row["instrument"]),
            total_score=row.get("total_score"),
            item_scores=row.get("item_scores"),
            is_complete=bool(row.get("is_complete", False)),
            source=str(row["source"]),
            item_citations=row.get("item_citations"),
            administered_at=row["administered_at"],
            created_by=str(row["created_by"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            deleted_at=row.get("deleted_at"),
        )
        self._session.add(orm_row)
        self._session.flush()
        return _row_to_dict(orm_row)

    def add(self, row: Row, user_id: str) -> Row:
        patient_id = str(row["patient_id"])
        if not self._has_access(patient_id, user_id):
            raise PatientOutcomeAccessDeniedError(patient_id, user_id)
        return self._insert(row)

    def add_self_report(self, row: Row) -> Row:
        return self._insert(row)

    def update(self, row: Row, user_id: str) -> Row:
        patient_id = str(row["patient_id"])
        if not self._has_access(patient_id, user_id):
            raise PatientOutcomeAccessDeniedError(patient_id, user_id)
        orm_row = self._session.get(OutcomeMeasureRow, str(row["id"]))
        if orm_row is None:
            return self.add(row, user_id)
        orm_row.total_score = row.get("total_score")
        orm_row.item_scores = row.get("item_scores")
        orm_row.is_complete = bool(row.get("is_complete", False))
        orm_row.source = str(row["source"])
        orm_row.item_citations = row.get("item_citations")
        orm_row.administered_at = row["administered_at"]
        orm_row.updated_at = row["updated_at"]
        orm_row.deleted_at = row.get("deleted_at")
        self._session.flush()
        return _row_to_dict(orm_row)
