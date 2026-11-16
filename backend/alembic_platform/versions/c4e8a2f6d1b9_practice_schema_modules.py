# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.schema_modules: every module, for every practice

Which schema modules a practice's schema holds (``app.db.schema_modules``).
Every practice holds every module, existing ones included: the column default
fills every current row, so there is no separate backfill, and a deployment
that never narrows it provisions and migrates exactly as before.

A CHECK keeps the core module, ``clinical``, in every row.

No PHI: a list of module names.

Idempotent, like the rest of this chain: the platform template already carries
the column on a fresh install.

Revision ID: c4e8a2f6d1b9
Revises: a3f8c1d6e924
Create Date: 2026-11-16
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4e8a2f6d1b9"
down_revision: str | Sequence[str] | None = "a3f8c1d6e924"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practices ADD COLUMN IF NOT EXISTS schema_modules text[]"
        " NOT NULL DEFAULT ARRAY['clinical', 'ai', 'portal'];"
    )
    op.execute(
        "DO $$ BEGIN"
        " IF NOT EXISTS (SELECT 1 FROM pg_constraint"
        "  WHERE conname = 'ck_practices_schema_modules_clinical'"
        "  AND conrelid = 'platform.practices'::regclass) THEN"
        "  ALTER TABLE platform.practices ADD CONSTRAINT ck_practices_schema_modules_clinical"
        "  CHECK ('clinical' = ANY (schema_modules));"
        " END IF;"
        " END $$;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practices"
        " DROP CONSTRAINT IF EXISTS ck_practices_schema_modules_clinical;"
    )
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS schema_modules;")
