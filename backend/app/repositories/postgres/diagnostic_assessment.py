# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL DiagnosticAssessmentRepository implementation.

Access checking mirrors the outcome-measure / notes repositories: the
per-patient predicate is delegated to the schema-local ``has_patient_access``
SQL function.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, or_, text

from ...db.models import DiagnosticAssessmentRow, PatientClinicianRow
from ...utcnow import utc_now
from ..diagnostic_assessment import (
    DiagnosticAssessmentRepository,
    PatientDiagnosticAccessDeniedError,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _row_to_dict(row: DiagnosticAssessmentRow) -> dict[str, object]:
    return {
        "id": row.id,
        "patient_id": row.patient_id,
        "session_id": row.session_id,
        "appointment_id": row.appointment_id,
        "instrument": row.instrument,
        "definition_version": row.definition_version,
        "criterion_responses": row.criterion_responses,
        "gate_responses": row.gate_responses,
        "meets_criteria": row.meets_criteria,
        "determined_icd10": row.determined_icd10,
        "diagnosis_label": row.diagnosis_label,
        "criterion_citations": row.criterion_citations,
        "source": row.source,
        "confirmed_at": row.confirmed_at,
        "assessed_at": row.assessed_at,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "deleted_at": row.deleted_at,
    }


class PostgresDiagnosticAssessmentRepository(DiagnosticAssessmentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL,
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    def get(self, assessment_id: str, user_id: str) -> dict[str, object] | None:
        row = (
            self._session.query(DiagnosticAssessmentRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == DiagnosticAssessmentRow.patient_id,
            )
            .filter(
                DiagnosticAssessmentRow.id == assessment_id,
                PatientClinicianRow.user_id == user_id,
                or_(
                    PatientClinicianRow.expires_at.is_(None),
                    PatientClinicianRow.expires_at > utc_now(),
                ),
            )
            .one_or_none()
        )
        return _row_to_dict(row) if row else None

    def list_by_patient(
        self,
        patient_id: str,
        user_id: str,
        *,
        instrument: str | None = None,
    ) -> list[dict[str, object]]:
        if not self._has_access(patient_id, user_id):
            return []
        query = self._session.query(DiagnosticAssessmentRow).filter(
            DiagnosticAssessmentRow.patient_id == patient_id,
        )
        if instrument is not None:
            query = query.filter(DiagnosticAssessmentRow.instrument == instrument)
        query = query.order_by(DiagnosticAssessmentRow.assessed_at.asc())
        return [_row_to_dict(r) for r in query.all()]

    def add(self, row: dict[str, object], user_id: str) -> dict[str, object]:
        patient_id = str(row["patient_id"])
        if not self._has_access(patient_id, user_id):
            raise PatientDiagnosticAccessDeniedError(patient_id, user_id)
        orm_row = DiagnosticAssessmentRow(
            id=str(row["id"]),
            patient_id=patient_id,
            session_id=row.get("session_id"),  # type: ignore[arg-type]
            appointment_id=row.get("appointment_id"),  # type: ignore[arg-type]
            instrument=str(row["instrument"]),
            definition_version=row["definition_version"],  # type: ignore[arg-type]
            criterion_responses=row.get("criterion_responses"),  # type: ignore[arg-type]
            gate_responses=row.get("gate_responses"),  # type: ignore[arg-type]
            meets_criteria=bool(row["meets_criteria"]),
            determined_icd10=row.get("determined_icd10"),  # type: ignore[arg-type]
            diagnosis_label=row.get("diagnosis_label"),  # type: ignore[arg-type]
            criterion_citations=row.get("criterion_citations"),  # type: ignore[arg-type]
            source=str(row["source"]),
            confirmed_at=row.get("confirmed_at"),  # type: ignore[arg-type]
            assessed_at=row["assessed_at"],  # type: ignore[arg-type]
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
            raise PatientDiagnosticAccessDeniedError(patient_id, user_id)
        orm_row = self._session.get(DiagnosticAssessmentRow, str(row["id"]))
        if orm_row is None:
            return self.add(row, user_id)
        orm_row.determined_icd10 = row.get("determined_icd10")  # type: ignore[assignment]
        orm_row.diagnosis_label = row.get("diagnosis_label")  # type: ignore[assignment]
        orm_row.confirmed_at = row.get("confirmed_at")  # type: ignore[assignment]
        orm_row.updated_at = row["updated_at"]  # type: ignore[assignment]
        orm_row.deleted_at = row.get("deleted_at")  # type: ignore[assignment]
        self._session.flush()
        return _row_to_dict(orm_row)
