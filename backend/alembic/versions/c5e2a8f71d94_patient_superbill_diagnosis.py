# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patients.superbill_includes_diagnosis: whether a superbill prints the diagnosis

A superbill with a diagnosis on it is one a client can file with their
insurer, and filing puts a mental-health diagnosis on that insurer's record.
Many clients who pay out of pocket do so precisely to keep it off, and want
the superbill as a receipt — for an HSA, an FSA, or their own records. So
whether the document carries the diagnosis is a per-client fact the
clinician records, not something the document decides.

``FALSE`` by default, and for every existing client: until someone says a
client is filing, the receipt leaves the diagnosis off.

Idempotent, like every revision in this chain.

Revision ID: c5e2a8f71d94
Revises: e9c4a71b5d28
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5e2a8f71d94"
down_revision: str | Sequence[str] | None = "e9c4a71b5d28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE patients "
        "ADD COLUMN IF NOT EXISTS superbill_includes_diagnosis BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS superbill_includes_diagnosis")
