# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Medication changes proposed from a note

``patient_medications.source_note_id`` is the note whose accepted proposal
last wrote the row (started it, stopped it, changed its dose or frequency).
NULL for a row entered on the chart directly, which every existing row was.

``note_chart_proposals.change`` holds a proposal's structured change for a
field whose proposals are actions on rows rather than text: for the
medication list, ``{"action", "drug_name", "dose", "frequency", "category",
"reason"}``. NULL for every free-text proposal, existing ones included.

Idempotent, like every revision in this chain.

Revision ID: e8c4a2f7d1b5
Revises: d7b2f4a9c613
Create Date: 2026-10-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e8c4a2f7d1b5"
down_revision: str | Sequence[str] | None = "d7b2f4a9c613"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        "ALTER TABLE patient_medications ADD COLUMN IF NOT EXISTS source_note_id UUID "
        "REFERENCES notes (id) ON DELETE SET NULL;"
    )
    op.execute("ALTER TABLE note_chart_proposals ADD COLUMN IF NOT EXISTS change JSONB;")


def downgrade() -> None:
    op.execute("ALTER TABLE note_chart_proposals DROP COLUMN IF EXISTS change;")
    op.execute("ALTER TABLE patient_medications DROP COLUMN IF EXISTS source_note_id;")
