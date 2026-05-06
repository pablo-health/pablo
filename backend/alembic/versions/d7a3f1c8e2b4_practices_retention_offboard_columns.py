"""platform.practices: audio_retention_days + offboard_scheduled_at + deleted_at

Brings three columns under OSS alembic management. They were originally
added by SaaS-side migrations (a7c4f8b2d319 audio retention, c5d8e2a1b049
offboard/deleted) so admin endpoints in pablo-saas could record per-practice
retention windows and offboarding state. Bringing them into OSS lets the
``PracticeRow`` ORM model expose them and removes the raw-SQL escape hatches
that pablo-saas had to use because the OSS model didn't declare the columns.

All DDL is idempotent (``ADD COLUMN IF NOT EXISTS`` and constraint/index
guards via ``DO $$``) so re-applying on a database where the SaaS chain
already created the columns is a no-op.

Columns
-------
* ``audio_retention_days`` — INTEGER NOT NULL DEFAULT 365, CHECK 30..2555.
  Per-practice retention window for recorded session audio. Range matches
  the privacy-policy commitment (30 days to 7 years).
* ``offboard_scheduled_at`` — TIMESTAMPTZ NULL. Set by the SaaS offboard
  endpoint to ``NOW() + grace_period_days``; cleared by writing NULL.
* ``deleted_at`` — TIMESTAMPTZ NULL. Set inside the offboard transaction
  once the practice schema is dropped.

Partial indexes on the timestamp columns exclude NULLs (the common case
for active tenants).

Revision ID: d7a3f1c8e2b4
Revises: e5b91c34a72f
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d7a3f1c8e2b4"
down_revision: str | Sequence[str] | None = "e5b91c34a72f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practices
            ADD COLUMN IF NOT EXISTS audio_retention_days INTEGER
            NOT NULL DEFAULT 365
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_practices_audio_retention_days_range'
            ) THEN
                ALTER TABLE platform.practices
                    ADD CONSTRAINT ck_practices_audio_retention_days_range
                    CHECK (audio_retention_days BETWEEN 30 AND 2555);
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        ALTER TABLE platform.practices
            ADD COLUMN IF NOT EXISTS offboard_scheduled_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        ALTER TABLE platform.practices
            ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_practices_offboard_scheduled_at
            ON platform.practices (offboard_scheduled_at)
            WHERE offboard_scheduled_at IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_practices_deleted_at
            ON platform.practices (deleted_at)
            WHERE deleted_at IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS platform.idx_practices_deleted_at")
    op.execute("DROP INDEX IF EXISTS platform.idx_practices_offboard_scheduled_at")
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS deleted_at")
    op.execute(
        "ALTER TABLE platform.practices DROP COLUMN IF EXISTS offboard_scheduled_at"
    )
    op.execute(
        "ALTER TABLE platform.practices "
        "DROP CONSTRAINT IF EXISTS ck_practices_audio_retention_days_range"
    )
    op.execute(
        "ALTER TABLE platform.practices DROP COLUMN IF EXISTS audio_retention_days"
    )
