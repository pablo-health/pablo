# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientIntakeSubmissionRepository implementation.

No ``has_patient_access`` call, unlike its clinician-side siblings: the
writer is the patient themselves. See the abstract base for why, and
``app.db.PATIENT_WRITABLE_TABLES`` for the policy that backs it at the
database layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import PatientIntakeSubmissionRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from ..patient_intake_submission import PatientIntakeSubmissionRepository


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
