# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointment_types: remember the place of service a type was last booked with

Adds ``appointment_types.last_place_of_service``, nullable, one of 11, 02 or
10. It is learned, not edited: a booking of a type that names one of those
codes writes that exact code onto the type, and a later booking of the type
that says nothing about where it happens takes the same code. A type taught
with 10 fills 10, never 02: the code is the clinician's own, and it reaches
the claim. ``NULL`` means the type has never been booked with one, and a
booking from it is left without a place of service, as before.

No backfill. Reading past appointments to guess a value would teach every
type from history nobody chose to teach it from; the first booking after
this lands does it.

Both statements are idempotent, because an earlier revision's
downgrade-and-replay can run this one again over a schema that has it.

Revision ID: c3e7a91f5d20
Revises: 48e739bd21f9
Create Date: 2026-11-09
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3e7a91f5d20"
down_revision: str | Sequence[str] | None = "48e739bd21f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ck_appointment_types_last_place_of_service"


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointment_types ADD COLUMN IF NOT EXISTS last_place_of_service VARCHAR(2)"
    )
    op.execute(f"ALTER TABLE appointment_types DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    op.execute(
        f"ALTER TABLE appointment_types ADD CONSTRAINT {CONSTRAINT_NAME} "
        "CHECK (last_place_of_service IS NULL OR last_place_of_service IN ('11', '02', '10'))"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE appointment_types DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    op.execute("ALTER TABLE appointment_types DROP COLUMN IF EXISTS last_place_of_service")
