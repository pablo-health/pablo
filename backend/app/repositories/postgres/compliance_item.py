# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL compliance-item repository — user-scoped."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from ...db.models import ComplianceItemRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class ComplianceItem:
    id: str
    user_id: str
    item_type: str
    label: str
    due_date: str | None
    notes: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PostgresComplianceItemRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_user(self, user_id: str) -> list[ComplianceItem]:
        rows = (
            self._session.query(ComplianceItemRow)
            .filter(ComplianceItemRow.user_id == user_id)
            .order_by(ComplianceItemRow.created_at)
            .all()
        )
        return [_row_to_item(r) for r in rows]

    def get(self, item_id: str, user_id: str) -> ComplianceItem | None:
        row = self._session.get(ComplianceItemRow, item_id)
        if row is None or row.user_id != user_id:
            return None
        return _row_to_item(row)

    def create(self, item: ComplianceItem) -> ComplianceItem:
        row = ComplianceItemRow()
        _item_to_row(item, row)
        self._session.add(row)
        self._session.flush()
        return item

    def update(self, item: ComplianceItem) -> ComplianceItem:
        row = self._session.get(ComplianceItemRow, item.id)
        if row is None or row.user_id != item.user_id:
            return self.create(item)
        _item_to_row(item, row)
        self._session.flush()
        return item

    def delete(self, item_id: str, user_id: str) -> bool:
        row = self._session.get(ComplianceItemRow, item_id)
        if row is None or row.user_id != user_id:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


def _row_to_item(row: ComplianceItemRow) -> ComplianceItem:
    return ComplianceItem(
        id=row.id,
        user_id=row.user_id,
        item_type=row.item_type,
        label=row.label,
        due_date=row.due_date,
        notes=row.notes,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _item_to_row(item: ComplianceItem, row: ComplianceItemRow) -> None:
    row.id = item.id
    row.user_id = item.user_id
    row.item_type = item.item_type
    row.label = item.label
    row.due_date = item.due_date
    row.notes = item.notes
    row.completed_at = item.completed_at
    row.created_at = item.created_at
    row.updated_at = item.updated_at
