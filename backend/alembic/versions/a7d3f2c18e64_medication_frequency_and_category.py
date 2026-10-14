# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Frequency and category on a medication

``frequency`` is how often and when the medication is taken, as written
("every morning", "50 mg AM / 25 mg PM"). It used to ride inside the dose
string, which meant a change of schedule could not be told apart from a
change of dose. ``category`` is ``psychiatric`` or ``other``, because a
prescriber's note lists the two separately.

Both are nullable and no existing row is touched: a medication recorded
before this revision has no frequency of its own and no category, which is
the truth about it. The check constraint holds for NULL, so adding it scans
the table without anything to correct. Idempotent, like every revision in
this chain.

Revision ID: a7d3f2c18e64
Revises: c9f3a6e2d817
Create Date: 2026-10-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7d3f2c18e64"
down_revision: str | Sequence[str] | None = "c9f3a6e2d817"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE patient_medications ADD COLUMN IF NOT EXISTS frequency TEXT;")
    op.execute("ALTER TABLE patient_medications ADD COLUMN IF NOT EXISTS category VARCHAR(16);")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_patient_medications_category'
                  AND conrelid = 'patient_medications'::regclass
            ) THEN
                ALTER TABLE patient_medications ADD CONSTRAINT ck_patient_medications_category
                    CHECK (category IN ('psychiatric', 'other'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE patient_medications DROP CONSTRAINT IF EXISTS ck_patient_medications_category;"
    )
    op.execute("ALTER TABLE patient_medications DROP COLUMN IF EXISTS category;")
    op.execute("ALTER TABLE patient_medications DROP COLUMN IF EXISTS frequency;")
