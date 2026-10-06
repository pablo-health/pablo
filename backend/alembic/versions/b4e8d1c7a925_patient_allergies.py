# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Allergies on the patient, with "no known drug allergies" as its own state

``allergy_status`` is one of ``not_recorded`` (nobody has entered anything),
``nkda`` or ``recorded``; ``allergies`` holds the entries, and has at least
one exactly when the status is ``recorded``. An empty list on its own could
not tell "none" from "never asked", which is why the status is a column.

Every existing patient starts at ``not_recorded`` with no entries, which is
what they are. Both constraints hold for that default, so adding them scans
the table without anything to correct and needs no row-level-security
bracket. Idempotent, like every revision in this chain.

Revision ID: b4e8d1c7a925
Revises: d5b8e2a71c94
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b4e8d1c7a925"
down_revision: str | Sequence[str] | None = "d5b8e2a71c94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS allergy_status VARCHAR(16) "
        "NOT NULL DEFAULT 'not_recorded';"
    )
    op.execute(
        "ALTER TABLE patients ADD COLUMN IF NOT EXISTS allergies JSONB NOT NULL DEFAULT '[]'::jsonb;"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_patients_allergy_status'
                  AND conrelid = 'patients'::regclass
            ) THEN
                ALTER TABLE patients ADD CONSTRAINT ck_patients_allergy_status
                    CHECK (allergy_status IN ('not_recorded', 'nkda', 'recorded'));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_patients_allergies_match_status'
                  AND conrelid = 'patients'::regclass
            ) THEN
                ALTER TABLE patients ADD CONSTRAINT ck_patients_allergies_match_status
                    CHECK ((allergy_status = 'recorded') = (jsonb_array_length(allergies) > 0));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP CONSTRAINT IF EXISTS ck_patients_allergies_match_status;")
    op.execute("ALTER TABLE patients DROP CONSTRAINT IF EXISTS ck_patients_allergy_status;")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS allergies;")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS allergy_status;")
