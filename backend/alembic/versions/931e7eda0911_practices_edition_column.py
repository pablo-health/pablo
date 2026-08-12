# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices: edition column

Adds ``edition`` so a practice can declare what kind of operator it is,
rather than downstream surfaces inferring it from which tables happen
to be empty.

* ``therapist`` — a clinical practice: patients, appointments, notes,
  charts. The default, so every pre-existing row and every new row
  that doesn't say otherwise reads as ``therapist`` with no change in
  behavior.
* ``personal`` — a non-clinical operator. No patients, no charts, no
  clinical severity floors.

String + CHECK, not a native enum, matching ``is_pentest`` /
``audio_retention_days`` on this table: a CHECK constraint is a plain
constraint swap when a new edition shows up, where a PG enum would need
``ALTER TYPE``. See ``app.models.enums.PracticeEdition`` for the
Python-side type.

``NOT NULL`` with a server default means no backfill pass and no
window where the column is null.

``platform.practices`` is bootstrapped from ORM metadata (not just
migrated) on every fresh environment, so declaring ``edition`` on
``PracticeRow`` means it can already exist by the time this migration
runs there. DDL is idempotent (``IF NOT EXISTS`` / ``DO $$`` guards),
same as ``d7a3f1c8e2b4``, so re-applying is a no-op.

Revision ID: 931e7eda0911
Revises: c8e3a91f4d6b
Create Date: 2026-08-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "931e7eda0911"
down_revision: str | Sequence[str] | None = "c8e3a91f4d6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practices
            ADD COLUMN IF NOT EXISTS edition VARCHAR(20)
            NOT NULL DEFAULT 'therapist'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_practices_edition'
            ) THEN
                ALTER TABLE platform.practices
                    ADD CONSTRAINT ck_practices_edition
                    CHECK (edition IN ('therapist', 'personal'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practices DROP CONSTRAINT IF EXISTS ck_practices_edition")
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS edition")
