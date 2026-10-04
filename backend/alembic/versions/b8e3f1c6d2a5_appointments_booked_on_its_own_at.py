# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Mark the sessions Pablo booked on its own from an event's title

Additive only: one nullable column. An image from before this revision ignores
it, so it keeps working against the migrated schema while a deploy rolls over.

* ``appointments.booked_on_its_own_at`` — when a session from a followed
  calendar or a calendar feed was booked without asking, because its title is
  the full name of exactly one client. Cleared once the clinician has seen it,
  which is what the calendar's "Pablo booked N sessions" list reads. NULL for
  every other booking, and for every appointment made before this revision.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: b8e3f1c6d2a5
Revises: a1d4e7b92c60
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b8e3f1c6d2a5"
down_revision: str | Sequence[str] | None = "a1d4e7b92c60"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS booked_on_its_own_at "
        "TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS booked_on_its_own_at")
