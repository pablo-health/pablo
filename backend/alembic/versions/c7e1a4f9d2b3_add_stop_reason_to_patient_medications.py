"""add stop_reason to patient_medications

Adds a nullable ``stop_reason`` free-text column to ``patient_medications``
so a clinician can record *why* a medication was discontinued (e.g.
ineffective, side effects, remission). Only meaningful for discontinued
rows; existing rows backfill to NULL.

Per-tenant table (lives inside each practice_{id} schema). The runner sets
search_path to the tenant schema before each invocation, so the unqualified
table reference resolves correctly.

Revision ID: c7e1a4f9d2b3
Revises: 566ad1e08aa2
Create Date: 2026-06-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c7e1a4f9d2b3"
down_revision: str | Sequence[str] | None = "566ad1e08aa2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patient_medications",
        sa.Column("stop_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("patient_medications", "stop_reason")
