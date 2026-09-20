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

from sqlalchemy import String, Uuid, bindparam, func, select, text
from sqlalchemy.exc import IntegrityError

from ...db.models import (
    PatientIntakeAssignmentRow,
    PatientIntakeResponseRow,
    PatientIntakeReviewEventRow,
)
from ..patient_intake_assignment import (
    ACTIVE_STATUSES,
    STATUS_TIMESTAMP_COLUMN,
    WRITABLE_STATUSES,
    PatientIntakeAssignmentRepository,
    ReceiptCollisionError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
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
        "receipt_code": row.receipt_code,
        "legacy_submission_id": row.legacy_submission_id,
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
        "provenance": row.provenance,
        "superseded_by": row.superseded_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _review_event_to_dict(row: PatientIntakeReviewEventRow) -> dict[str, object]:
    return {
        "id": row.id,
        "assignment_id": row.assignment_id,
        "patient_id": row.patient_id,
        "kind": row.kind,
        "item_ids": list(row.item_ids or []),
        "note_to_patient": row.note_to_patient,
        "created_by": row.created_by,
        "created_at": row.created_at,
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
            receipt_code=None,
            legacy_submission_id=None,
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

    def list_responses_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> list[dict[str, object]]:
        row = self._session.get(PatientIntakeAssignmentRow, assignment_id)
        if row is None or not self._has_access(row.patient_id, user_id):
            return []
        return self._live_responses(assignment_id, row.patient_id, drafts_only=False)

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

    def get_live_response_for_clinician(
        self, assignment_id: str, user_id: str, item_id: str
    ) -> dict[str, object] | None:
        assignment = self._session.get(PatientIntakeAssignmentRow, assignment_id)
        if assignment is None or not self._has_access(assignment.patient_id, user_id):
            return None
        return self.get_live_response(assignment_id, assignment.patient_id, item_id)

    def add_clinician_response(
        self, row: dict[str, object], user_id: str, *, supersedes: str | None
    ) -> dict[str, object] | None:
        if not self._has_access(str(row["patient_id"]), user_id):
            return None
        return self._write_successor(row, supersedes)

    def add_review_event(self, row: dict[str, object], user_id: str) -> dict[str, object] | None:
        if not self._has_access(str(row["patient_id"]), user_id):
            return None
        return self._write_review_event(row)

    def list_review_events_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> list[dict[str, object]]:
        assignment = self._session.get(PatientIntakeAssignmentRow, assignment_id)
        if assignment is None or not self._has_access(assignment.patient_id, user_id):
            return []
        return self._events(assignment_id, assignment.patient_id)

    def count_superseded_responses_for_clinician(
        self, assignment_id: str, user_id: str
    ) -> dict[str, int]:
        assignment = self._session.get(PatientIntakeAssignmentRow, assignment_id)
        if assignment is None or not self._has_access(assignment.patient_id, user_id):
            return {}
        rows = self._session.execute(
            select(PatientIntakeResponseRow.item_id, func.count())
            .where(
                PatientIntakeResponseRow.assignment_id == assignment_id,
                PatientIntakeResponseRow.patient_id == assignment.patient_id,
                PatientIntakeResponseRow.superseded_by.is_not(None),
            )
            .group_by(PatientIntakeResponseRow.item_id)
        ).all()
        return {str(item_id): int(count) for item_id, count in rows}

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
        return self._live_responses(assignment_id, patient_id, drafts_only=True)

    def list_live_responses(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        return self._live_responses(assignment_id, patient_id, drafts_only=False)

    def get_live_response(
        self, assignment_id: str, patient_id: str, item_id: str
    ) -> dict[str, object] | None:
        # A draft first when there is one: a live draft and a frozen answer
        # can coexist for the same question once a correction reopens it,
        # and the draft is the row a save would touch.
        row = (
            self._session.execute(
                select(PatientIntakeResponseRow)
                .where(
                    PatientIntakeResponseRow.assignment_id == assignment_id,
                    PatientIntakeResponseRow.patient_id == patient_id,
                    PatientIntakeResponseRow.item_id == item_id,
                    PatientIntakeResponseRow.superseded_by.is_(None),
                )
                .order_by(
                    PatientIntakeResponseRow.draft.desc(),
                    PatientIntakeResponseRow.updated_at.desc(),
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        return _response_to_dict(row) if row else None

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
            provenance=str(row.get("provenance") or "patient"),
            superseded_by=None,
            created_at=row["created_at"],  # type: ignore[arg-type]
            updated_at=row["updated_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _response_to_dict(orm_row)

    def add_successor_response(
        self, row: dict[str, object], *, supersedes: str
    ) -> dict[str, object]:
        return self._write_successor(row, supersedes)

    def add_patient_review_event(self, row: dict[str, object]) -> dict[str, object]:
        return self._write_review_event(row)

    def list_review_events_for_patient_principal(
        self, assignment_id: str, patient_id: str
    ) -> list[dict[str, object]]:
        return self._events(assignment_id, patient_id)

    def retire_draft_responses(
        self,
        assignment_id: str,
        patient_id: str,
        item_ids: Sequence[str],
        now: datetime,
    ) -> list[str]:
        if not item_ids:
            return []
        rows = (
            self._session.execute(
                select(PatientIntakeResponseRow).where(
                    PatientIntakeResponseRow.assignment_id == assignment_id,
                    PatientIntakeResponseRow.patient_id == patient_id,
                    PatientIntakeResponseRow.item_id.in_(tuple(item_ids)),
                    PatientIntakeResponseRow.draft.is_(True),
                    PatientIntakeResponseRow.superseded_by.is_(None),
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.superseded_by = row.id
            row.updated_at = now
        self._session.flush()
        return [row.item_id for row in rows]

    def freeze_draft_responses(self, assignment_id: str, patient_id: str, now: datetime) -> int:
        rows = (
            self._session.execute(
                select(PatientIntakeResponseRow).where(
                    PatientIntakeResponseRow.assignment_id == assignment_id,
                    PatientIntakeResponseRow.patient_id == patient_id,
                    PatientIntakeResponseRow.draft.is_(True),
                    PatientIntakeResponseRow.superseded_by.is_(None),
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.draft = False
            row.updated_at = now
        self._session.flush()
        return len(rows)

    def mark_submitted(
        self, assignment_id: str, patient_id: str, *, now: datetime, receipt_code: str
    ) -> dict[str, object] | None:
        row = self._session.execute(
            select(PatientIntakeAssignmentRow).where(
                PatientIntakeAssignmentRow.id == assignment_id,
                PatientIntakeAssignmentRow.patient_id == patient_id,
                PatientIntakeAssignmentRow.status.in_(tuple(WRITABLE_STATUSES)),
            )
        ).scalar_one_or_none()
        if row is None:
            return None

        # A savepoint, so a receipt that is already spoken for leaves the
        # surrounding transaction usable and the caller can offer another
        # one. Without it the failed flush would poison everything the
        # request had already written, including the frozen answers.
        savepoint = self._session.begin_nested()
        row.status = "submitted"
        row.submitted_at = now
        row.updated_at = now
        row.receipt_code = receipt_code
        try:
            self._session.flush()
        except IntegrityError as exc:
            savepoint.rollback()
            raise ReceiptCollisionError(receipt_code) from exc
        savepoint.commit()
        return _assignment_to_dict(row)

    # --- helpers ---

    def _write_successor(self, row: dict[str, object], supersedes: str | None) -> dict[str, object]:
        """Insert the new answer, then point the one it replaces at it.

        This order, not the other one: ``superseded_by`` names an id, so the
        row it names has to exist first. The replaced row keeps its value
        and its ``draft`` flag — the pointer is the only thing that changes
        on it, which is what lets a handed-in form still read back as it was
        handed in.

        The partial unique index admits both rows: it is built on live
        DRAFTS, and what is being superseded here is always a frozen answer.
        """
        orm_row = PatientIntakeResponseRow(
            id=str(row["id"]),
            assignment_id=str(row["assignment_id"]),
            patient_id=str(row["patient_id"]),
            item_id=str(row["item_id"]),
            value=row["value"],  # type: ignore[arg-type]
            draft=bool(row["draft"]),
            provenance=str(row["provenance"]),
            superseded_by=None,
            created_at=row["created_at"],  # type: ignore[arg-type]
            updated_at=row["updated_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()

        if supersedes is not None:
            previous = self._session.get(PatientIntakeResponseRow, supersedes)
            if previous is not None:
                previous.superseded_by = orm_row.id
                self._session.flush()
        return _response_to_dict(orm_row)

    def _write_review_event(self, row: dict[str, object]) -> dict[str, object]:
        orm_row = PatientIntakeReviewEventRow(
            id=str(row["id"]),
            assignment_id=str(row["assignment_id"]),
            patient_id=str(row["patient_id"]),
            kind=str(row["kind"]),
            item_ids=list(row["item_ids"]),  # type: ignore[call-overload]
            note_to_patient=row["note_to_patient"],  # type: ignore[arg-type]
            created_by=str(row["created_by"]) if row.get("created_by") else None,
            created_at=row["created_at"],  # type: ignore[arg-type]
        )
        self._session.add(orm_row)
        self._session.flush()
        return _review_event_to_dict(orm_row)

    def _events(self, assignment_id: str, patient_id: str) -> list[dict[str, object]]:
        rows = (
            self._session.execute(
                select(PatientIntakeReviewEventRow)
                .where(
                    PatientIntakeReviewEventRow.assignment_id == assignment_id,
                    PatientIntakeReviewEventRow.patient_id == patient_id,
                )
                .order_by(
                    PatientIntakeReviewEventRow.created_at,
                    PatientIntakeReviewEventRow.id,
                )
            )
            .scalars()
            .all()
        )
        return [_review_event_to_dict(row) for row in rows]

    def _live_responses(
        self, assignment_id: str, patient_id: str, *, drafts_only: bool
    ) -> list[dict[str, object]]:
        statement = select(PatientIntakeResponseRow).where(
            PatientIntakeResponseRow.assignment_id == assignment_id,
            PatientIntakeResponseRow.patient_id == patient_id,
            PatientIntakeResponseRow.superseded_by.is_(None),
        )
        if drafts_only:
            statement = statement.where(PatientIntakeResponseRow.draft.is_(True))
        rows = (
            self._session.execute(statement.order_by(PatientIntakeResponseRow.item_id))
            .scalars()
            .all()
        )
        return [_response_to_dict(row) for row in rows]

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        result = self._session.execute(
            _HAS_PATIENT_ACCESS_SQL, {"pid": patient_id, "uid": user_id}
        ).scalar()
        return bool(result)
