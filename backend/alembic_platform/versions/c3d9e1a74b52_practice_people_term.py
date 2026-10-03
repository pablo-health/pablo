# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.people_term: what the practice calls the people it sees

A practice-wide default for the word the app uses, "clients" or "patients".
``NULL`` means the practice has not chosen, which reads as "clients". Each
clinician's own choice comes before it; see ``app.people_term``.

No PHI: a single vocabulary choice.

**Written idempotently, and expected to be a no-op** on the paths that
materialise the platform tables from the models before this chain runs — the
same reasoning as ``d1c7b94e3a26_portal_practice_slugs``.

Revision ID: c3d9e1a74b52
Revises: b7e2c4f9a031
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3d9e1a74b52"
down_revision: str | Sequence[str] | None = "b7e2c4f9a031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform.practices ADD COLUMN IF NOT EXISTS people_term VARCHAR(10);")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_practices_people_term'
                  AND conrelid = 'platform.practices'::regclass
            ) THEN
                ALTER TABLE platform.practices
                    ADD CONSTRAINT ck_practices_people_term
                    CHECK (people_term IN ('clients', 'patients'));
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practices DROP CONSTRAINT IF EXISTS ck_practices_people_term;")
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS people_term;")
