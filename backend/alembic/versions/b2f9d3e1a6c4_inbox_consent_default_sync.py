# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""keep the inbox quality-review column's server default in lockstep with the model

The column was added (with ``DEFAULT FALSE``) one revision earlier, but the model
declared only a Python-side default. Declaring ``server_default`` on the model
brings it in line with the schema so a metadata-built schema (``create_all``, used
by some test harnesses) produces the same NOT NULL DEFAULT FALSE column an
inserter can rely on. This revision re-asserts that default so the model change
ships with a migration (guardrail #4); it is idempotent — the column already
carries the default from the prior revision.

Revision ID: b2f9d3e1a6c4
Revises: e6a1c9d4b7f2
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b2f9d3e1a6c4"
down_revision: str | Sequence[str] | None = "e6a1c9d4b7f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users ALTER COLUMN inbox_quality_review_opt_in SET DEFAULT false"
    )


def downgrade() -> None:
    # No-op: the column and its default are owned by the prior revision
    # (e6a1c9d4b7f2); there is nothing to undo here.
    pass
