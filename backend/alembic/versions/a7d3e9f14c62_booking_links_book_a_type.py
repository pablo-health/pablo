# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.booking_links books an appointment type.

Revision ID: a7d3e9f14c62
Revises: c4e8b1f7a2d9
Create Date: 2026-09-10

A booking link used to carry its own ``duration_minutes`` and a free-text
``session_type``. Both were second answers to a question the appointment type
already answers, and neither could be enforced: a link could say "Intake"
while pointing at nothing, and nothing decided who was allowed to book it.

Now a link holds a required ``appointment_type_id`` and nothing else about the
appointment. Length comes from the type; who may book comes from the type's
switches and the practice policy.

Public booking has never been enabled in a production deployment, so there is
no installed base to carry. Rows that exist in the old shape (development
only) cannot be given a type after the fact and are removed rather than
backfilled; recreate them. The new column is NOT NULL from the start.

``booking_links`` is platform-scoped, so this migration has no bearing on the
per-tenant schema and needs no tenant template regeneration.

Written idempotently: ``alembic/env.py`` materialises the platform tables from
the models with ``create_all`` before this chain runs, so on a fresh database
the table already has the new shape when this revision executes and every
statement below is a no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7d3e9f14c62"
down_revision: str | Sequence[str] | None = "c4e8b1f7a2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'platform'
                  AND table_name = 'booking_links'
                  AND column_name = 'appointment_type_id'
            ) THEN
                -- Old-shape rows have no type to carry forward.
                DELETE FROM platform.booking_links;
                ALTER TABLE platform.booking_links
                    ADD COLUMN appointment_type_id UUID NOT NULL;
            END IF;
        END $$;
        """
    )
    op.execute(
        "ALTER TABLE platform.booking_links DROP CONSTRAINT IF EXISTS ck_booking_links_duration"
    )
    op.execute("ALTER TABLE platform.booking_links DROP COLUMN IF EXISTS duration_minutes")
    op.execute("ALTER TABLE platform.booking_links DROP COLUMN IF EXISTS session_type")


def downgrade() -> None:
    # The old columns come back with defaults so existing typed rows survive;
    # the type reference is dropped because the old shape had nowhere to keep it.
    op.execute(
        "ALTER TABLE platform.booking_links "
        "ADD COLUMN IF NOT EXISTS duration_minutes INTEGER NOT NULL DEFAULT 50"
    )
    op.execute(
        "ALTER TABLE platform.booking_links "
        "ADD COLUMN IF NOT EXISTS session_type VARCHAR(20) NOT NULL DEFAULT 'individual'"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_booking_links_duration'
            ) THEN
                ALTER TABLE platform.booking_links
                    ADD CONSTRAINT ck_booking_links_duration
                    CHECK (duration_minutes BETWEEN 5 AND 480);
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE platform.booking_links DROP COLUMN IF EXISTS appointment_type_id")
