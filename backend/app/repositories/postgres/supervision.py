# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL supervision-relationship repository — user-scoped.

Backs the structured oversight relationships a clinician must keep
current (physician delegation, NP collaborative, PA supervision,
pre-licensure clinical supervision) plus an accrued-hour log. The
relationship's review deadline rides a ``compliance_items`` row so it
reuses the existing reminder machinery — ``create_relationship`` can
create and link that item in the same flush.

All rows are PHI-free and owned by the clinician via ``user_id``;
every read/write is scoped to the caller's ``user_id`` so a user only
ever touches their own relationships and hour entries.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from ...db.models import (
    ComplianceItemRow,
    SupervisionHoursRow,
    SupervisionRelationshipRow,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class SupervisionRelationship:
    id: str
    user_id: str
    compliance_item_id: str | None
    relationship_type: str
    supervisor_name: str
    supervisor_credential: str | None
    supervisor_dea: str | None
    supervisor_license: str | None
    state: str | None
    effective_date: str | None
    review_cadence_days: int | None
    next_review_date: str | None
    authority_ref: str | None
    status: str
    notes: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class SupervisionHours:
    id: str
    supervision_relationship_id: str
    user_id: str
    logged_date: str
    hours: Decimal
    kind: str
    supervisor: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class PostgresSupervisionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    def create_relationship(
        self,
        relationship: SupervisionRelationship,
        *,
        review_item_label: str | None = None,
    ) -> SupervisionRelationship:
        """Persist a relationship, optionally creating its review item.

        When ``review_item_label`` is supplied and the relationship has a
        ``next_review_date`` but no ``compliance_item_id`` yet, a
        ``compliance_items`` row is created in the same flush and linked
        so the review deadline rides the existing reminder machinery
        (the item's ``due_date`` mirrors ``next_review_date``).
        """
        if (
            review_item_label is not None
            and relationship.compliance_item_id is None
            and relationship.next_review_date is not None
        ):
            now = datetime.now(UTC)
            item = ComplianceItemRow()
            item.id = str(uuid.uuid4())
            item.user_id = relationship.user_id
            item.item_type = "supervision_review"
            item.label = review_item_label
            item.due_date = relationship.next_review_date
            item.notes = None
            item.completed_at = None
            item.created_at = now
            item.updated_at = now
            self._session.add(item)
            self._session.flush()
            relationship.compliance_item_id = item.id

        row = SupervisionRelationshipRow()
        _relationship_to_row(relationship, row)
        self._session.add(row)
        self._session.flush()
        return relationship

    def list_by_user(self, user_id: str) -> list[SupervisionRelationship]:
        rows = (
            self._session.query(SupervisionRelationshipRow)
            .filter(SupervisionRelationshipRow.user_id == user_id)
            .order_by(SupervisionRelationshipRow.created_at)
            .all()
        )
        return [_row_to_relationship(r) for r in rows]

    def get(self, relationship_id: str, user_id: str) -> SupervisionRelationship | None:
        row = self._session.get(SupervisionRelationshipRow, relationship_id)
        if row is None or row.user_id != user_id:
            return None
        return _row_to_relationship(row)

    def update(self, relationship: SupervisionRelationship) -> SupervisionRelationship:
        row = self._session.get(SupervisionRelationshipRow, relationship.id)
        if row is None or row.user_id != relationship.user_id:
            return self.create_relationship(relationship)
        _relationship_to_row(relationship, row)
        self._session.flush()
        return relationship

    def delete(self, relationship_id: str, user_id: str) -> bool:
        row = self._session.get(SupervisionRelationshipRow, relationship_id)
        if row is None or row.user_id != user_id:
            return False
        self._session.delete(row)
        self._session.flush()
        return True

    # ------------------------------------------------------------------
    # Accrued hours
    # ------------------------------------------------------------------

    def add_hours(self, entry: SupervisionHours) -> SupervisionHours:
        row = SupervisionHoursRow()
        _hours_to_row(entry, row)
        self._session.add(row)
        self._session.flush()
        return entry

    def list_hours(
        self,
        relationship_id: str,
        user_id: str,
    ) -> list[SupervisionHours]:
        rows = (
            self._session.query(SupervisionHoursRow)
            .filter(
                SupervisionHoursRow.supervision_relationship_id == relationship_id,
                SupervisionHoursRow.user_id == user_id,
            )
            .order_by(SupervisionHoursRow.logged_date)
            .all()
        )
        return [_row_to_hours(r) for r in rows]


def _row_to_relationship(row: SupervisionRelationshipRow) -> SupervisionRelationship:
    return SupervisionRelationship(
        id=row.id,
        user_id=row.user_id,
        compliance_item_id=row.compliance_item_id,
        relationship_type=row.relationship_type,
        supervisor_name=row.supervisor_name,
        supervisor_credential=row.supervisor_credential,
        supervisor_dea=row.supervisor_dea,
        supervisor_license=row.supervisor_license,
        state=row.state,
        effective_date=row.effective_date,
        review_cadence_days=row.review_cadence_days,
        next_review_date=row.next_review_date,
        authority_ref=row.authority_ref,
        status=row.status,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _relationship_to_row(
    relationship: SupervisionRelationship,
    row: SupervisionRelationshipRow,
) -> None:
    row.id = relationship.id
    row.user_id = relationship.user_id
    row.compliance_item_id = relationship.compliance_item_id
    row.relationship_type = relationship.relationship_type
    row.supervisor_name = relationship.supervisor_name
    row.supervisor_credential = relationship.supervisor_credential
    row.supervisor_dea = relationship.supervisor_dea
    row.supervisor_license = relationship.supervisor_license
    row.state = relationship.state
    row.effective_date = relationship.effective_date
    row.review_cadence_days = relationship.review_cadence_days
    row.next_review_date = relationship.next_review_date
    row.authority_ref = relationship.authority_ref
    row.status = relationship.status
    row.notes = relationship.notes
    row.created_at = relationship.created_at
    row.updated_at = relationship.updated_at


def _row_to_hours(row: SupervisionHoursRow) -> SupervisionHours:
    return SupervisionHours(
        id=row.id,
        supervision_relationship_id=row.supervision_relationship_id,
        user_id=row.user_id,
        logged_date=row.logged_date,
        hours=row.hours,
        kind=row.kind,
        supervisor=row.supervisor,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _hours_to_row(entry: SupervisionHours, row: SupervisionHoursRow) -> None:
    row.id = entry.id
    row.supervision_relationship_id = entry.supervision_relationship_id
    row.user_id = entry.user_id
    row.logged_date = entry.logged_date
    row.hours = entry.hours
    row.kind = entry.kind
    row.supervisor = entry.supervisor
    row.notes = entry.notes
    row.created_at = entry.created_at
    row.updated_at = entry.updated_at
