# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_sites.last_previewed_at

When a practice's website draft was last previewed: set when a preview address
is made and when the preview serves the draft's ``index.html``, at most once a
minute. ``NULL`` on every existing row: no preview has been recorded yet.

Idempotent, like the rest of this chain: the platform template already
carries the column on a fresh install.

No PHI: a time a practice looked at its own public website's draft.

Revision ID: a3f8c1d6e924
Revises: b7e2d9a4c615
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a3f8c1d6e924"
down_revision: str | Sequence[str] | None = "b7e2d9a4c615"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practice_sites "
        "ADD COLUMN IF NOT EXISTS last_previewed_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practice_sites DROP COLUMN IF EXISTS last_previewed_at")
