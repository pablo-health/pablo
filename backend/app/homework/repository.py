# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Homework assignments: what the clinician assigned, and what the client marked done.

Both arms scope every query by the one ``patient_id`` the route already
proved: the clinician routes through the chart's own patient lookup (the
``patient_clinicians`` grant), the portal routes from the patient principal.
Row security tests both again underneath — ``has_patient_access`` for a
clinician, and for a patient the status test that keeps a withdrawn
assignment off the portal (``app.db._patient_principal_predicate_for``).

Suggestions are not stored here: they are chart proposals of the ``homework``
family (``note_chart_proposals``), decided with the other chart updates. An
accepted one arrives through :meth:`HomeworkRepository.assign_suggestion`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from ..db.models import HomeworkAssignmentRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

#: Statuses a client sees.
CLIENT_STATUSES = ("assigned", "done")


@dataclass(frozen=True)
class Assignment:
    id: str
    patient_id: str
    note_id: str | None
    proposal_id: str | None
    source_field: str | None
    proposed_text: str | None
    text: str
    status: str
    origin: str
    assigned_at: datetime
    completed_at: datetime | None
    client_note: str | None
    created_at: datetime
    updated_at: datetime


def _assignment(row: HomeworkAssignmentRow) -> Assignment:
    return Assignment(
        id=row.id,
        patient_id=row.patient_id,
        note_id=row.note_id,
        proposal_id=row.proposal_id,
        source_field=row.source_field,
        proposed_text=row.proposed_text,
        text=row.body,
        status=row.status,
        origin=row.origin,
        assigned_at=row.assigned_at,
        completed_at=row.completed_at,
        client_note=row.client_note,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class HomeworkRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- clinician arm ---

    def list_for_patient(self, patient_id: str) -> list[Assignment]:
        rows = self._session.scalars(
            select(HomeworkAssignmentRow)
            .where(HomeworkAssignmentRow.patient_id == patient_id)
            .order_by(HomeworkAssignmentRow.assigned_at.desc())
        ).all()
        return [_assignment(r) for r in rows]

    def add(self, patient_id: str, text: str, user_id: str, at: datetime) -> Assignment:
        row = HomeworkAssignmentRow(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            body=text,
            status="assigned",
            origin="clinician",
            assigned_by=user_id,
            assigned_at=at,
            created_at=at,
            updated_at=at,
        )
        self._session.add(row)
        self._session.flush()
        return _assignment(row)

    def assign_suggestion(  # noqa: PLR0913 — one keyword per thing the assignment records
        self,
        patient_id: str,
        *,
        text: str,
        proposed_text: str,
        note_id: str | None,
        proposal_id: str,
        source_field: str,
        user_id: str,
    ) -> None:
        """An accepted homework suggestion, as the assignment the client sees.

        Once per suggestion: the chart proposal's own decision is conditional,
        and the unique ``proposal_id`` holds even if that were bypassed.
        """
        now = utc_now()
        self._session.execute(
            insert(HomeworkAssignmentRow)
            .values(
                id=str(uuid.uuid4()),
                patient_id=patient_id,
                note_id=note_id,
                proposal_id=proposal_id,
                source_field=source_field,
                proposed_text=proposed_text,
                body=text,
                status="assigned",
                origin="note",
                assigned_by=user_id,
                assigned_at=now,
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(
                index_elements=["proposal_id"],
                index_where=HomeworkAssignmentRow.proposal_id.is_not(None),
            )
        )

    def get(self, assignment_id: str, patient_id: str) -> Assignment | None:
        row = self._session.scalar(
            select(HomeworkAssignmentRow).where(
                HomeworkAssignmentRow.id == assignment_id,
                HomeworkAssignmentRow.patient_id == patient_id,
            )
        )
        return None if row is None else _assignment(row)

    def transition(
        self,
        assignment_id: str,
        patient_id: str,
        *,
        from_statuses: tuple[str, ...],
        values: dict[str, Any],
    ) -> Assignment | None:
        """Move one row on, only from ``from_statuses``. ``None`` when it was not there.

        Conditional in the UPDATE itself, so two people acting at once cannot
        both succeed: the second matches no row.
        """
        moved = self._session.execute(
            update(HomeworkAssignmentRow)
            .where(
                HomeworkAssignmentRow.id == assignment_id,
                HomeworkAssignmentRow.patient_id == patient_id,
                HomeworkAssignmentRow.status.in_(from_statuses),
            )
            .values(**values)
            .returning(HomeworkAssignmentRow)
        ).scalar()
        return None if moved is None else _assignment(moved)

    # --- patient arm ---

    def list_for_client(self, patient_id: str) -> list[Assignment]:
        """What the client sees: open assignments first, newest first."""
        rows = self._session.scalars(
            select(HomeworkAssignmentRow)
            .where(
                HomeworkAssignmentRow.patient_id == patient_id,
                HomeworkAssignmentRow.status.in_(CLIENT_STATUSES),
            )
            .order_by(
                (HomeworkAssignmentRow.status == "done").asc(),
                HomeworkAssignmentRow.assigned_at.desc(),
            )
        ).all()
        return [_assignment(r) for r in rows]
