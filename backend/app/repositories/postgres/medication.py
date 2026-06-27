# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL MedicationRepository implementation.

Access checking mirrors the notes and outcome_measures repositories: every
method delegates the per-patient access predicate to the schema-local
``has_patient_access`` SQL function (migration ``777b846ab944``), which
reads ``patient_clinicians`` and short-circuits at the DB layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, case, nulls_last, or_, select, text

from ...db.models import PatientClinicianRow, PatientMedicationRow
from ...utcnow import utc_now
from ..medication import MedicationRepository, PatientMedicationAccessDeniedError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _row_to_dict(row: PatientMedicationRow) -> dict[str, object]:
    return {
        "id": row.id,
        "patient_id": row.patient_id,
        "drug_name": row.drug_name,
        "dose": row.dose,
        "status": row.status,
        "started_at": row.started_at,
        "stopped_at": row.stopped_at,
        "stop_reason": row.stop_reason,
        "notes": row.notes,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
    }


class PostgresMedicationRepository(MedicationRepository):
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

    def get(self, medication_id: str, user_id: str) -> dict[str, object] | None:
        """Fetch by id with access check, or ``None`` if absent/denied."""
        row = self._session.execute(
            select(PatientMedicationRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == PatientMedicationRow.patient_id,
            )
            .where(
                PatientMedicationRow.id == medication_id,
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
        ).scalar_one_or_none()
        return _row_to_dict(row) if row else None

    def list_by_patient(
        self,
        patient_id: str,
        user_id: str,
        *,
        status: str | None = None,
    ) -> list[dict[str, object]]:
        if not self._has_access(patient_id, user_id):
            return []
        query = select(PatientMedicationRow).where(
            PatientMedicationRow.patient_id == patient_id,
            PatientMedicationRow.deleted_at.is_(None),
        )
        if status is not None:
            query = query.where(PatientMedicationRow.status == status)
        # Active first, then other statuses; within each group started_at desc nulls last
        query = query.order_by(
            case((PatientMedicationRow.status == "active", 0), else_=1),
            nulls_last(PatientMedicationRow.started_at.desc()),
        )
        return [_row_to_dict(r) for r in self._session.execute(query).scalars().all()]

    # --- writes ---

    def create(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._has_access(patient_id, user_id):
            raise PatientMedicationAccessDeniedError(patient_id, user_id)
        orm_row = PatientMedicationRow(
            id=str(row["id"]),
            patient_id=patient_id,
            drug_name=str(row["drug_name"]),
            dose=str(row["dose"]),
            status=str(row["status"]),
            started_at=row.get("started_at"),  # type: ignore[arg-type]
            stopped_at=row.get("stopped_at"),  # type: ignore[arg-type]
            stop_reason=row.get("stop_reason"),  # type: ignore[arg-type]
            notes=row.get("notes"),  # type: ignore[arg-type]
            created_by=str(row["created_by"]),
            created_at=row["created_at"],  # type: ignore[arg-type]
            updated_at=row["updated_at"],  # type: ignore[arg-type]
            deleted_at=row.get("deleted_at"),  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _row_to_dict(orm_row)

    def update(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._has_access(patient_id, user_id):
            raise PatientMedicationAccessDeniedError(patient_id, user_id)
        orm_row = self._session.get(PatientMedicationRow, str(row["id"]))
        if orm_row is None:
            return self.create(row, user_id)
        orm_row.drug_name = str(row["drug_name"])
        orm_row.dose = str(row["dose"])
        orm_row.status = str(row["status"])
        orm_row.started_at = row.get("started_at")  # type: ignore[assignment]
        orm_row.stopped_at = row.get("stopped_at")  # type: ignore[assignment]
        orm_row.stop_reason = row.get("stop_reason")  # type: ignore[assignment]
        orm_row.notes = row.get("notes")  # type: ignore[assignment]
        orm_row.updated_at = row["updated_at"]  # type: ignore[assignment]
        orm_row.deleted_at = row.get("deleted_at")  # type: ignore[assignment]
        self._session.flush()
        return _row_to_dict(orm_row)

    def soft_delete(self, medication_id: str, user_id: str) -> None:
        orm_row = self._session.execute(
            select(PatientMedicationRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == PatientMedicationRow.patient_id,
            )
            .where(
                PatientMedicationRow.id == medication_id,
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
        ).scalar_one_or_none()
        if orm_row is None:
            # No row or no access — treat as access denied to be safe
            raise PatientMedicationAccessDeniedError(medication_id, user_id)
        now = utc_now()
        orm_row.deleted_at = now
        orm_row.updated_at = now
        self._session.flush()
