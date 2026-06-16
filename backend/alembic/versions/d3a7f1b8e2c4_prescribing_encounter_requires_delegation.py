# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""prescribing_encounters gains requires_delegation

Snapshots, at encounter create, whether the prescriber operates under a
supervisory / collaborative (delegation) agreement. ``False`` marks an
independent prescriber, so delegation-only ledger items don't apply to the
encounter; ``NULL`` (legacy rows, or no credential signal) preserves the
ruleset's default behavior. The checklist sync consults this column so the
distinction survives every re-sync.

Unqualified table name resolves via search_path to the active practice schema
(the ``practice`` template at deploy time, each tenant during the per-tenant
fan-out). ``ADD COLUMN IF NOT EXISTS`` makes the statement a no-op on schemas
that already have the column, so it is safe to re-run under the fan-out.

Revision ID: d3a7f1b8e2c4
Revises: c4b9f2e7a610
Create Date: 2026-06-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "d3a7f1b8e2c4"
down_revision: str | Sequence[str] | None = "c4b9f2e7a610"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE prescribing_encounters ADD COLUMN IF NOT EXISTS requires_delegation BOOLEAN"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE prescribing_encounters DROP COLUMN IF EXISTS requires_delegation")
