# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refill requests carry the time the practice means to answer by

``refill_requests`` gains ``due_at``: the end of the last working day of the
practice's turnaround target, fixed when the request arrives. NULL on every
existing row, which the queue sorts by when it was asked; nothing is
backfilled, because the target those requests would be measured against did
not exist when they arrived.

Idempotent, like every revision in this chain.

Revision ID: d2c8f4a1b693
Revises: b4e7c2a9f136
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d2c8f4a1b693"
down_revision: str | Sequence[str] | None = "b4e7c2a9f136"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute("ALTER TABLE refill_requests ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ;")


def downgrade() -> None:
    op.execute("ALTER TABLE refill_requests DROP COLUMN IF EXISTS due_at;")
