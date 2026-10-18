# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tasks can be tied to a client, and filed from a session

``tasks`` gains ``patient_id``, nullable: a task about one client ("send Omar
the release form Friday") names them, and every other task names nobody. The
foreign key is ``ON DELETE SET NULL``, so deleting a client leaves the
clinician's task in place, unlinked.

The row policy is unchanged. ``enable_rls_on_schema`` picks the ``user_id``
shape before it looks at ``patient_id``, so a task stays its owner's alone and
a co-treating clinician never sees it. Access to the client is checked where a
task is linked or read, by the same lookup the chart uses.

``ix_tasks_user_patient_id`` is what the client's chart reads their open tasks
through; partial, since most tasks name no client.

``ck_tasks_source`` admits ``session``: a task the clinician accepted from
what they said they would do in a session.

Every existing row keeps ``patient_id`` NULL, so nothing is backfilled.
Downgrade drops the column and its index and puts the old source list back; a
task filed from a session survives it as a manual one.

Idempotent, like every revision in this chain.

Revision ID: e3a8c5f1d702
Revises: b4e7c2a9f136
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e3a8c5f1d702"
down_revision: str | Sequence[str] | None = "b4e7c2a9f136"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS patient_id UUID "
        "REFERENCES patients(id) ON DELETE SET NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tasks_user_patient_id ON tasks (user_id, patient_id) "
        "WHERE patient_id IS NOT NULL"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_source")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_source "
        "CHECK (source IN ('manual', 'reminder', 'plan', 'import', 'session'))"
    )


def downgrade() -> None:
    op.execute("UPDATE tasks SET source = 'manual' WHERE source = 'session'")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_source")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_source "
        "CHECK (source IN ('manual', 'reminder', 'plan', 'import'))"
    )
    op.execute("DROP INDEX IF EXISTS ix_tasks_user_patient_id")
    # Dropping the column drops its foreign key with it.
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS patient_id")
