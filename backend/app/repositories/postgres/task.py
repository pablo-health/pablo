# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL task repository — user-scoped.

A task is anything the clinician has to do, compliance reminders included
(``category = 'compliance'``). Every read and write is scoped to one user, the
same contract the row policy enforces underneath: another user's task is
``None`` / ``False`` here, never an error that says it exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from ...db.models import TaskRow

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class Task:
    id: str
    user_id: str
    item_type: str
    label: str
    due_date: date | None
    notes: str | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Defaulted so code written against the compliance item, which had none
    # of these, still builds a valid compliance task.
    category: str = "compliance"
    source: str = "manual"
    parent_task_id: str | None = None
    goal_key: str | None = None
    repeat_rule: str | None = None


@dataclass(frozen=True)
class TaskFilter:
    """Narrows a list. ``None`` on a field means "any"."""

    category: str | None = None
    parent_task_id: str | None = None
    goal_key: str | None = None
    source: str | None = None


class PostgresTaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_by_user(self, user_id: str, filters: TaskFilter | None = None) -> list[Task]:
        query = select(TaskRow).where(TaskRow.user_id == user_id)
        f = filters or TaskFilter()
        if f.category is not None:
            query = query.where(TaskRow.category == f.category)
        if f.parent_task_id is not None:
            query = query.where(TaskRow.parent_task_id == f.parent_task_id)
        if f.goal_key is not None:
            query = query.where(TaskRow.goal_key == f.goal_key)
        if f.source is not None:
            query = query.where(TaskRow.source == f.source)
        rows = self._session.execute(query.order_by(TaskRow.created_at)).scalars().all()
        return [_row_to_task(r) for r in rows]

    def get(self, task_id: str, user_id: str) -> Task | None:
        row = self._session.get(TaskRow, task_id)
        if row is None or row.user_id != user_id:
            return None
        return _row_to_task(row)

    def create(self, task: Task) -> Task:
        row = TaskRow()
        _task_to_row(task, row)
        self._session.add(row)
        self._session.flush()
        return task

    def update(self, task: Task) -> Task:
        row = self._session.get(TaskRow, task.id)
        if row is None or row.user_id != task.user_id:
            return self.create(task)
        _task_to_row(task, row)
        self._session.flush()
        return task

    def delete(self, task_id: str, user_id: str) -> bool:
        row = self._session.get(TaskRow, task_id)
        if row is None or row.user_id != user_id:
            return False
        self._session.delete(row)
        self._session.flush()
        return True


class PostgresComplianceItemRepository(PostgresTaskRepository):
    """The compliance view of the same rows.

    Its list is the compliance tasks only, which is all a caller written
    before tasks existed expects to see. ``get``, ``update`` and ``delete``
    reach any task by id, so a link to a task works whichever view holds it.
    """

    def list_by_user(self, user_id: str, filters: TaskFilter | None = None) -> list[Task]:
        f = filters or TaskFilter()
        return super().list_by_user(
            user_id,
            TaskFilter(
                category="compliance",
                parent_task_id=f.parent_task_id,
                goal_key=f.goal_key,
                source=f.source,
            ),
        )


def _row_to_task(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        user_id=row.user_id,
        item_type=row.item_type,
        label=row.label,
        due_date=row.due_date,
        notes=row.notes,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        category=row.category,
        source=row.source,
        parent_task_id=row.parent_task_id,
        goal_key=row.goal_key,
        repeat_rule=row.repeat_rule,
    )


def _task_to_row(task: Task, row: TaskRow) -> None:
    # ``source_ref`` is not on the dataclass and is never written here: it is
    # the automatic filers' handle on their own rows, and no edit may move it.
    row.id = task.id
    row.user_id = task.user_id
    row.item_type = task.item_type
    row.label = task.label
    row.due_date = task.due_date
    row.notes = task.notes
    row.completed_at = task.completed_at
    row.created_at = task.created_at
    row.updated_at = task.updated_at
    row.category = task.category
    row.source = task.source
    row.parent_task_id = task.parent_task_id
    row.goal_key = task.goal_key
    row.repeat_rule = task.repeat_rule
