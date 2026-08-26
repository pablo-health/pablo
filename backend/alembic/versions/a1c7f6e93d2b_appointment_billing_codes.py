# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments billing-code columns

Lets a clinician record what was billed for a visit: a CPT service code,
up to four modifiers, a unit count, a place-of-service code, and an
ordered ICD-10 diagnosis list (first code is primary). All five columns
are NULL by default so existing appointments keep working unchanged, and
nothing populates them automatically — they are set only by an explicit
clinician action, either while creating a note for the visit or via a
standalone edit on the appointment.

Revision ID: a1c7f6e93d2b
Revises: c7e2b81f40a9
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a1c7f6e93d2b"
down_revision: str | Sequence[str] | None = "c7e2b81f40a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS service_code VARCHAR(10) NULL")
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS modifiers JSONB NULL")
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS unit_count INTEGER NULL")
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS place_of_service VARCHAR(2) NULL")
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS diagnosis_codes JSONB NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS diagnosis_codes")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS place_of_service")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS unit_count")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS modifiers")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS service_code")
