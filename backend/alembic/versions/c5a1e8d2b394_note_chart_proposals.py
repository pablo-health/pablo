# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Note chart proposals: the chart updates a note proposes, and what was decided

``note_chart_proposals`` holds one row per note per chart field (and, for a
list field such as allergies, per entry): the proposed text, a line saying
what changed, the transcript segments it cites, and the clinician's decision
(pending, accepted, edited or discarded) with who decided and when. It sits
beside the note rather than in its content, so nothing that reads a note's
content ever sees a proposal.

The table carries ``patient_id`` and no ``user_id``, so the reconcile pass
that follows every migration gives it the ``has_patient_access`` row policy,
like ``patient_chart_history``. Idempotent, like every revision in this
chain.

Revision ID: c5a1e8d2b394
Revises: b4e81c6d2f90
Create Date: 2026-10-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5a1e8d2b394"
down_revision: str | Sequence[str] | None = "b4e81c6d2f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS note_chart_proposals (
            id             UUID         PRIMARY KEY,
            note_id        UUID         NOT NULL
                REFERENCES notes (id) ON DELETE CASCADE,
            patient_id     UUID         NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            field_key      VARCHAR(64)  NOT NULL,
            item_key       VARCHAR(200) NOT NULL DEFAULT '',
            proposed_text  TEXT         NOT NULL,
            what_changed   TEXT         NOT NULL,
            evidence       JSONB        NOT NULL DEFAULT '[]'::jsonb,
            origin         VARCHAR(16)  NOT NULL,
            decision       VARCHAR(16)  NOT NULL DEFAULT 'pending',
            decided_text   TEXT,
            decided_by     UUID,
            decided_at     TIMESTAMPTZ,
            created_at     TIMESTAMPTZ  NOT NULL,
            CONSTRAINT uq_note_chart_proposals_field UNIQUE (note_id, field_key, item_key),
            CONSTRAINT ck_note_chart_proposals_decision
                CHECK (decision IN ('pending', 'accepted', 'edited', 'discarded'))
        );
        """
    )
    # The unique constraint's index serves reads by note; no second index.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS note_chart_proposals;")
