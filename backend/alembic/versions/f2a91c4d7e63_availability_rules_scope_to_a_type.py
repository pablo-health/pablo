# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""availability_rules scope to an appointment type, and may claim their window

Two columns, both optional and both defaulting to what every existing rule
already means.

``appointment_type_id`` narrows a rule to one kind of appointment. NULL — the
default, and what every row written before this migration says — is the
practice-wide rule that governs every type, so nothing about an installed rule
set changes. It is what turns "at most two appointments a day" into "at most
two intakes a day" without inventing a rule type to say it. The foreign key
CASCADES: a cap on intakes that quietly became a cap on everything because
somebody deleted the type would be a far worse surprise than the rule going
with it.

``allow_other_types`` is meaningful on a type-scoped working_hours rule and
says whether other types may be offered inside that window. TRUE is the
default and is today's behaviour — narrowing a type's hours takes nothing away
from anyone else. FALSE turns the window into a claim the other types are
subtracted from.

Revision ID: f2a91c4d7e63
Revises: e1b7a3d95c48
Create Date: 2026-09-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f2a91c4d7e63"
down_revision: str | Sequence[str] | None = "e1b7a3d95c48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE availability_rules ADD COLUMN IF NOT EXISTS appointment_type_id UUID")
    op.execute(
        "ALTER TABLE availability_rules "
        "ADD COLUMN IF NOT EXISTS allow_other_types BOOLEAN NOT NULL DEFAULT TRUE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_availability_rules_appointment_type_id "
        "ON availability_rules (appointment_type_id)"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'availability_rules_appointment_type_id_fkey'
                  AND conrelid = 'availability_rules'::regclass
            ) THEN
                ALTER TABLE availability_rules
                    ADD CONSTRAINT availability_rules_appointment_type_id_fkey
                    FOREIGN KEY (appointment_type_id)
                    REFERENCES appointment_types (id) ON DELETE CASCADE;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE availability_rules "
        "DROP CONSTRAINT IF EXISTS availability_rules_appointment_type_id_fkey"
    )
    op.execute("DROP INDEX IF EXISTS ix_availability_rules_appointment_type_id")
    op.execute("ALTER TABLE availability_rules DROP COLUMN IF EXISTS allow_other_types")
    op.execute("ALTER TABLE availability_rules DROP COLUMN IF EXISTS appointment_type_id")
