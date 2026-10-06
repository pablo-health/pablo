# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""client_present_end_seconds on therapy_sessions

Seconds into the recording when the client was last present. What the
clinician dictates after the client leaves is drafted into the note but is
not face-to-face time, so minutes are measured to this point rather than to
``ended_at``. ``0`` marks a dictation-only recording; NULL means unknown,
which every session recorded before this column existed is.

Additive only, no backfill. Idempotent: fanned out once per practice schema.

Revision ID: b6c2e8f41a07
Revises: d5b8e2a71c94
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b6c2e8f41a07"
down_revision: str | Sequence[str] | None = "d5b8e2a71c94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE therapy_sessions "
        "ADD COLUMN IF NOT EXISTS client_present_end_seconds DOUBLE PRECISION NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE therapy_sessions DROP COLUMN IF EXISTS client_present_end_seconds")
