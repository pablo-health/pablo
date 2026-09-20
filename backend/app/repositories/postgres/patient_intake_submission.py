# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientIntakeSubmissionRepository implementation.

The patient-principal methods make no ``has_patient_access`` call, unlike
their clinician-side siblings: the writer is the patient themselves. See
the abstract base for why, and ``app.db.PATIENT_WRITABLE_TABLES`` for the
policy that backs it at the database layer.

:meth:`PostgresPatientIntakeSubmissionRepository.list_for_clinician` is the
clinician-side sibling, and does ask — same schema-local
``has_patient_access`` function (migration ``777b846ab944``) the notes and
outcome-measure repositories use, so a grant means the same thing on this
table as on the rest of the chart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, select, text

from ...db.models import PatientIntakeSubmissionRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from ..patient_intake_submission import PatientIntakeSubmissionRepository

_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _row_to_dict(row: PatientIntakeSubmissionRow) -> dict[str, object]:
    return {
        "id": row.id,
        "patient_id": row.patient_id,
        "submitted_at": row.submitted_at,
        "payload": row.payload,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


class PostgresPatientIntakeSubmissionRepository(PatientIntakeSubmissionRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_for_patient_principal(self, row: dict[str, object]) -> dict[str, object]:
        orm_row = PatientIntakeSubmissionRow(
            id=str(row["id"]),
            patient_id=str(row["patient_id"]),
            submitted_at=row["submitted_at"],  # type: ignore[arg-type]
            payload=row["payload"],  # type: ignore[arg-type]
            created_by=str(row["created_by"]),
            created_at=row["created_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _row_to_dict(orm_row)

    def get_for_patient_principal(
        self, submission_id: str, patient_id: str
    ) -> dict[str, object] | None:
        row = self._session.execute(
            select(PatientIntakeSubmissionRow).where(
                PatientIntakeSubmissionRow.id == submission_id,
                PatientIntakeSubmissionRow.patient_id == patient_id,
            )
        ).scalar_one_or_none()
        return _row_to_dict(row) if row else None

    def list_for_clinician(self, patient_id: str, user_id: str) -> list[dict[str, object]]:
        if not self._has_access(patient_id, user_id):
            return []
        rows = (
            self._session.execute(
                select(PatientIntakeSubmissionRow)
                .where(PatientIntakeSubmissionRow.patient_id == patient_id)
                .order_by(PatientIntakeSubmissionRow.submitted_at.desc())
            )
            .scalars()
            .all()
        )
        return [_row_to_dict(row) for row in rows]

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL, {"pid": patient_id, "uid": user_id}
        ).scalar()
        return bool(result)
