# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""diagnostic_assessments.meets_criteria nullable

``checklist`` evaluator definitions record the clinician's responses but make
no algorithmic pass/fail determination, so ``meets_criteria`` is NULL for those
rows. Drop the NOT NULL constraint on the per-tenant
``diagnostic_assessments`` table (lives in each ``practice_{id}`` schema).

See PABLO-6xj.8.

Revision ID: c4e8d1f6a2b9
Revises: b7e2f4a1c9d3
Create Date: 2026-06-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4e8d1f6a2b9"
down_revision: str | Sequence[str] | None = "b7e2f4a1c9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE diagnostic_assessments ALTER COLUMN meets_criteria DROP NOT NULL")


def downgrade() -> None:
    # Backfill any checklist rows (NULL) to false before restoring NOT NULL so
    # the downgrade is safe on a tenant that has recorded checklist assessments.
    op.execute(
        "UPDATE diagnostic_assessments SET meets_criteria = false WHERE meets_criteria IS NULL"
    )
    op.execute("ALTER TABLE diagnostic_assessments ALTER COLUMN meets_criteria SET NOT NULL")
