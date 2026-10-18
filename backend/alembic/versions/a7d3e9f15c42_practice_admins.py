# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""practice_admins

The clinicians a practice owner has made practice admins. An admin may change
practice-wide settings alongside the owner; only the owner grants and revokes.
The owner is never a row here: ownership is on the practice row.

Registered not-row-scoped in ``app.db``: the owner reads every row, so a
"your own rows" policy on ``user_id`` would hide the very list it manages.
The practice schema is the boundary.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: a7d3e9f15c42
Revises: d7a3f19c4e25
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7d3e9f15c42"
down_revision: str | Sequence[str] | None = "d7a3f19c4e25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS practice_admins (
            user_id    UUID         PRIMARY KEY,
            granted_by UUID         NOT NULL,
            granted_at TIMESTAMPTZ  NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS practice_admins CASCADE;")
