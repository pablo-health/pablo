# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""compliance_items becomes tasks: one table for everything with a date

Clinicians want reminders for things that are not compliance, and want a goal
broken into dated steps. Both are the same shape as a compliance reminder — a
title, a due date, done or not — so the table is generalised rather than
joined by a second one with the same columns.

Every existing row is kept, with its id, and becomes a task with
``category = 'compliance'`` and ``source = 'manual'``. The evidence documents,
supervision reviews and anything else holding a foreign key to these rows
keeps pointing at them: Postgres follows a renamed table, so nothing that
references it is rewritten.

New columns:

* ``category`` — ``compliance`` by default, which is what every writer that
  predates tasks files.
* ``source`` — ``manual``, ``reminder``, ``plan`` or ``import``.
* ``parent_task_id`` — the task this one is a step of. ``ON DELETE CASCADE``:
  deleting a plan deletes its steps rather than leaving them on the dashboard
  with nothing to belong to.
* ``goal_key`` — an opaque link to a goal held elsewhere.
* ``repeat_rule`` — ``weekly``, ``monthly``, ``quarterly`` or ``yearly``;
  NULL happens once. There was no per-row recurrence before (a compliance
  template's cadence lives in code), so existing rows get NULL.

The primary key and the two indexes are renamed with the table, so a migrated
practice and one provisioned from the template carry the same names.

The row policy needs nothing here. ``enable_rls_on_schema`` keys it on the
``user_id`` column, not on the table name, and a policy stays on its table
through a rename.

Downgrade puts the old name back and drops the new columns. A task from
another category survives it as a compliance item with no template behind it,
which the old code shows undated and unclassified rather than losing.

Idempotent, like every revision in this chain.

Revision ID: d7a3f19c4e25
Revises: c5e2a8f71d94
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d7a3f19c4e25"
down_revision: str | Sequence[str] | None = "c5e2a8f71d94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (old, new) for every object whose name carries the table's. Inlined, so a
#: later rename in the models cannot change what this revision did.
_RENAMES = (
    ("ix_compliance_items_user_id", "ix_tasks_user_id"),
    ("ux_compliance_items_source_ref", "ux_tasks_source_ref"),
)


def _rename_index(old: str, new: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('{old}') IS NOT NULL AND to_regclass('{new}') IS NULL THEN
                ALTER INDEX {old} RENAME TO {new};
            END IF;
        END $$
        """
    )


_PKEY_TO_TASKS = """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'compliance_items_pkey' AND conrelid = to_regclass('tasks')
        ) THEN
            ALTER TABLE tasks RENAME CONSTRAINT compliance_items_pkey TO tasks_pkey;
        END IF;
    END $$
"""

_PKEY_TO_COMPLIANCE_ITEMS = """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conname = 'tasks_pkey' AND conrelid = to_regclass('tasks')
        ) THEN
            ALTER TABLE tasks RENAME CONSTRAINT tasks_pkey TO compliance_items_pkey;
        END IF;
    END $$
"""


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('compliance_items') IS NOT NULL
               AND to_regclass('tasks') IS NULL THEN
                ALTER TABLE compliance_items RENAME TO tasks;
            END IF;
        END $$
        """
    )
    op.execute(_PKEY_TO_TASKS)
    for old, new in _RENAMES:
        _rename_index(old, new)

    # NOT NULL with a default fills every existing row in the same statement:
    # each one is a compliance item someone typed in.
    op.execute(
        "ALTER TABLE tasks "
        "ADD COLUMN IF NOT EXISTS category VARCHAR(32) NOT NULL DEFAULT 'compliance'"
    )
    op.execute(
        "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'manual'"
    )
    op.execute(
        "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS parent_task_id UUID "
        "REFERENCES tasks(id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS goal_key VARCHAR(128)")
    op.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS repeat_rule VARCHAR(16)")

    op.execute("CREATE INDEX IF NOT EXISTS ix_tasks_parent_task_id ON tasks (parent_task_id)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tasks_user_goal_key ON tasks (user_id, goal_key) "
        "WHERE goal_key IS NOT NULL"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_source")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_source "
        "CHECK (source IN ('manual', 'reminder', 'plan', 'import'))"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_repeat_rule")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_repeat_rule "
        "CHECK (repeat_rule IS NULL OR repeat_rule IN ('weekly', 'monthly', 'quarterly', 'yearly'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_repeat_rule")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_source")
    op.execute("DROP INDEX IF EXISTS ix_tasks_user_goal_key")
    op.execute("DROP INDEX IF EXISTS ix_tasks_parent_task_id")
    # Dropping the column drops its self-referencing foreign key with it.
    for column in ("repeat_rule", "goal_key", "parent_task_id", "source", "category"):
        op.execute(f"ALTER TABLE tasks DROP COLUMN IF EXISTS {column}")

    for old, new in _RENAMES:
        _rename_index(new, old)
    op.execute(_PKEY_TO_COMPLIANCE_ITEMS)
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('tasks') IS NOT NULL
               AND to_regclass('compliance_items') IS NULL THEN
                ALTER TABLE tasks RENAME TO compliance_items;
            END IF;
        END $$
        """
    )
