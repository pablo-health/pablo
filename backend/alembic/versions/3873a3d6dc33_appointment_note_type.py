# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments.note_type — persist the note type chosen at booking time

The booking form lets a clinician pick a note type ("Used when you start the
session") but the value had nowhere to live — it reset to the default every
time the appointment was reopened. This gives ``appointments`` its own
``note_type`` column, mirroring ``notes.note_type``, so the choice survives
reload/edit and seeds the session (and its note) when the appointment is
started.

Revision ID: 3873a3d6dc33
Revises: a1c7f6e93d2b
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "3873a3d6dc33"
down_revision: str | Sequence[str] | None = "a1c7f6e93d2b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointments "
        "ADD COLUMN IF NOT EXISTS note_type VARCHAR(30) NOT NULL DEFAULT 'soap'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS note_type")
