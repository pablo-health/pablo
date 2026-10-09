# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note chart-proposal runs: what the call proposed that was considered and not offered

``note_chart_proposal_runs.considered`` is a JSON list, one entry per history
proposal the materiality check (``app.chart_proposals.materiality``) did not
offer: the field, the text, the cited lines and the reason. It is what an
evaluation reads, and what a view of "considered, not offered" would. Empty for
every run before this one. Idempotent, like every revision in this chain.

Revision ID: a3d8f1c6e902
Revises: e8c4a2f7d1b5
Create Date: 2026-10-09
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a3d8f1c6e902"
down_revision: str | Sequence[str] | None = "e8c4a2f7d1b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        ALTER TABLE note_chart_proposal_runs
            ADD COLUMN IF NOT EXISTS considered JSONB NOT NULL DEFAULT '[]'::jsonb;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE note_chart_proposal_runs DROP COLUMN IF EXISTS considered;")
