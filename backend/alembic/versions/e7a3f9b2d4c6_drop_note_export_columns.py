# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""drop note export columns

``notes`` carried six columns for an export review queue that no longer
exists: ``export_status``, ``export_queued_at``, ``export_reviewed_at``,
``export_reviewed_by``, ``exported_at`` and ``redacted_export_payload``.
Nothing reads or writes them. ``redacted_content`` and
``naturalized_content`` are still in use and stay.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: e7a3f9b2d4c6
Revises: c5d2e8f1a743
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e7a3f9b2d4c6"
down_revision: str | Sequence[str] | None = "c5d2e8f1a743"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "export_status",
    "export_queued_at",
    "export_reviewed_at",
    "export_reviewed_by",
    "exported_at",
    "redacted_export_payload",
)


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    for column in _COLUMNS:
        op.execute(f"ALTER TABLE notes DROP COLUMN IF EXISTS {column}")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS export_status "
        "VARCHAR(20) NOT NULL DEFAULT 'not_queued'"
    )
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS export_queued_at TIMESTAMPTZ")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS export_reviewed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS export_reviewed_by UUID")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS exported_at TIMESTAMPTZ")
    op.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS redacted_export_payload JSONB")
