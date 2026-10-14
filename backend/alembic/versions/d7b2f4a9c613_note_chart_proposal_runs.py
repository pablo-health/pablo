# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note chart-proposal runs: whether a note's proposal call ran, and how it ended

``note_chart_proposal_runs`` holds one row per note: ``ok``, ``failed`` or
``skipped``, the exception type when it failed (never its message), and
when. A note with no proposals because the call failed must not read the
same as one where nothing changed.

The table carries ``patient_id`` and no ``user_id``, so the reconcile pass
that follows every migration gives it the ``has_patient_access`` row policy.
Idempotent, like every revision in this chain.

Revision ID: d7b2f4a9c613
Revises: c5a1e8d2b394
Create Date: 2026-10-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d7b2f4a9c613"
down_revision: str | Sequence[str] | None = "c5a1e8d2b394"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS note_chart_proposal_runs (
            note_id      UUID         PRIMARY KEY
                REFERENCES notes (id) ON DELETE CASCADE,
            patient_id   UUID         NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            status       VARCHAR(16)  NOT NULL,
            error_class  VARCHAR(100),
            computed_at  TIMESTAMPTZ  NOT NULL,
            CONSTRAINT ck_note_chart_proposal_runs_status
                CHECK (status IN ('ok', 'failed', 'skipped'))
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS note_chart_proposal_runs;")
