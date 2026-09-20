# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL PatientIntakeAssignmentRepository implementation.

Tenant scope is the session's ``search_path``, set before the request
reaches here. Within a tenant, the two principals are separated as the
abstract base describes: the clinician-side methods ask the schema-local
``has_patient_access`` function (migration ``777b846ab944``) the notes and
outcome-measure repositories use, so a grant means the same thing on these
tables as on the rest of the chart; the patient-principal methods ask
nothing and filter on the id their caller took off the authenticated
principal, backed by the ``app.current_patient_id`` policy underneath.

Row security is the floor, not the ceiling. Every query below also carries
its own predicate, so a missing GUC is a wrong answer from the database
rather than a wide one from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, Uuid, bindparam, select, text

from ...db.models import PatientIntakeAssignmentRow, PatientIntakeResponseRow
from ..patient_intake_assignment import (
    ACTIVE_STATUSES,
    STATUS_TIMESTAMP_COLUMN,
    PatientIntakeAssignmentRepository,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

_HAS_PATIENT_ACCESS_SQL = text("SELECT has_patient_access(:pid, :uid)").bindparams(
    bindparam("pid", type_=Uuid(as_uuid=False)),
    bindparam("uid", type_=String()),
)


def _assignment_to_dict(row: PatientIntakeAssignmentRow) -> dict[str, object]:
    return {
        "id": row.id,
        "patient_id": row.patient_id,
        "version_id": row.version_id,
        "status": row.status,
        "assigned_by": row.assigned_by,
        "assigned_at": row.assigned_at,
        "submitted_at": row.submitted_at,
        "accepted_at": row.accepted_at,
        "withdrawn_at": row.withdrawn_at,
        "updated_at": row.updated_at,
    }


def _response_to_dict(row: PatientIntakeResponseRow) -> dict[str, object]:
    return {
        "id": row.id,
        "assignment_id": row.assignment_id,
        "patient_id": row.patient_id,
        "item_id": row.item_id,
        "value": row.value,
        "draft": row.draft,
        "superseded_by": row.superseded_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class PostgresPatientIntakeAssignmentRepository(PatientIntakeAssignmentRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- clinician side ---

    def add_assignment(self, row: dict[str, object], user_id: str) -> dict[str, object] | None:
        patient_id = str(row["patient_id"])
        if not self._has_access(patient_id, user_id):
            return None
        orm_row = PatientIntakeAssignmentRow(
            id=str(row["id"]),
            patient_id=patient_id,
            version_id=str(row["version_id"]),
            status=str(row["status"]),
            assigned_by=str(row["assigned_by"]) if row.get("assigned_by") else None,
            assigned_at=row["assigned_at"],  # type: ignore[arg-type]
            submitted_at=None,
            accepted_at=None,
            withdrawn_at=None,
            updated_at=row["updated_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _assignment_to_dict(orm_row)

    def list_assignments_for_clinician(
        self, patient_id: str, user_id: str
    ) -> list[dict[str, object]]:
        if not self._has_access(patient_id, user_id):
            return []
        rows = (
            self._session.execute(
                select(PatientIntakeAssignmentRow)
                .where(PatientIntakeAssignmentRow.patient_id == patient_id)
                .order_by(
                    PatientIntakeAssignmentRow.assigned_at.desc(),
                    PatientIntakeAssignmentRow.id,
                )
            )
            .scalars()
            .all()
        )
        return [_assignment_to_dict(row) for row in rows]

    def get_assignment_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> dict[str, object] | None:
        row = self._session.get(PatientIntakeAssignmentRow, assignment_id)
        if row is None or not self._has_access(row.patient_id, user_id):
            return None
        return _assignment_to_dict(row)

    def find_active_assignment(
        self, patient_id: str, version_id: str, user_id: str
    ) -> dict[str, object] | None:
        if not self._has_access(patient_id, user_id):
            return None
        row = (
            self._session.execute(
                select(PatientIntakeAssignmentRow)
                .where(
                    PatientIntakeAssignmentRow.patient_id == patient_id,
                    PatientIntakeAssignmentRow.version_id == version_id,
                    PatientIntakeAssignmentRow.status.in_(ACTIVE_STATUSES),
                )
                .order_by(PatientIntakeAssignmentRow.assigned_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _assignment_to_dict(row) if row else None

    def set_status_for_clinician(
        self, assignment_id: str, user_id: str, *, status: str, now: datetime
    ) -> dict[str, object] | None:
        row = self._session.get(PatientIntakeAssignmentRow, assignment_id)
        if row is None or not self._has_access(row.patient_id, user_id):
            return None
        row.status = status
        row.updated_at = now
        stamped = STATUS_TIMESTAMP_COLUMN.get(status)
        if stamped is not None:
            setattr(row, stamped, now)
        self._session.flush()
        return _assignment_to_dict(row)

    # --- patient side ---

    def list_assignments_for_patient_principal(self, patient_id: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(PatientIntakeAssignmentRow)
                .where(PatientIntakeAssignmentRow.patient_id == patient_id)
                .order_by(
                    PatientIntakeAssignmentRow.assigned_at.desc(),
                    PatientIntakeAssignmentRow.id,
                )
            )
            .scalars()
            .all()
        )
        return [_assignment_to_dict(row) for row in rows]

    def get_assignment_for_patient_principal(
        self, assignment_id: str, patient_id: str
    ) -> dict[str, object] | None:
        row = self._session.execute(
            select(PatientIntakeAssignmentRow).where(
                PatientIntakeAssignmentRow.id == assignment_id,
                PatientIntakeAssignmentRow.patient_id == patient_id,
            )
        ).scalar_one_or_none()
        return _assignment_to_dict(row) if row else None

    def record_save(
        self, assignment_id: str, patient_id: str, now: datetime
    ) -> dict[str, object] | None:
        row = self._session.execute(
            select(PatientIntakeAssignmentRow).where(
                PatientIntakeAssignmentRow.id == assignment_id,
                PatientIntakeAssignmentRow.patient_id == patient_id,
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.status == "assigned":
            row.status = "in_progress"
        row.updated_at = now
        self._session.flush()
        return _assignment_to_dict(row)

    def list_draft_responses(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(PatientIntakeResponseRow)
                .where(
                    PatientIntakeResponseRow.assignment_id == assignment_id,
                    PatientIntakeResponseRow.patient_id == patient_id,
                    PatientIntakeResponseRow.draft.is_(True),
                    PatientIntakeResponseRow.superseded_by.is_(None),
                )
                .order_by(PatientIntakeResponseRow.item_id)
            )
            .scalars()
            .all()
        )
        return [_response_to_dict(row) for row in rows]

    def save_draft_response(self, row: dict[str, object]) -> dict[str, object]:
        existing = self._session.execute(
            select(PatientIntakeResponseRow).where(
                PatientIntakeResponseRow.assignment_id == str(row["assignment_id"]),
                PatientIntakeResponseRow.patient_id == str(row["patient_id"]),
                PatientIntakeResponseRow.item_id == str(row["item_id"]),
                PatientIntakeResponseRow.draft.is_(True),
                PatientIntakeResponseRow.superseded_by.is_(None),
            )
        ).scalar_one_or_none()

        if existing is not None:
            existing.value = row["value"]  # type: ignore[assignment]
            existing.updated_at = row["updated_at"]  # type: ignore[assignment]
            self._session.flush()
            return _response_to_dict(existing)

        orm_row = PatientIntakeResponseRow(
            id=str(row["id"]),
            assignment_id=str(row["assignment_id"]),
            patient_id=str(row["patient_id"]),
            item_id=str(row["item_id"]),
            value=row["value"],  # type: ignore[arg-type]
            draft=True,
            superseded_by=None,
            created_at=row["created_at"],  # type: ignore[arg-type]
            updated_at=row["updated_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _response_to_dict(orm_row)

    # --- helpers ---

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL, {"pid": patient_id, "uid": user_id}
        ).scalar()
        return bool(result)
