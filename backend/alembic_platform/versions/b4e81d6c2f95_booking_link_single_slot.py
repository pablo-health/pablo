# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.booking_links single-slot columns

A booking link can be scoped to one slot and used once. ``slot_start_at`` /
``slot_end_at`` are the window it books; ``slot_claimed_at`` is when it was
used. All three are ``NULL`` on every existing row, which is an ordinary link.

Idempotent, like the rest of this chain: the platform template already
carries the columns on a fresh install.

No PHI: two times on a clinician's diary and when a link was used.

Revision ID: b4e81d6c2f95
Revises: f2d9c7b3a641
Create Date: 2026-10-22
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b4e81d6c2f95"
down_revision: str | Sequence[str] | None = "f2d9c7b3a641"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("slot_start_at", "slot_end_at", "slot_claimed_at")


def upgrade() -> None:
    for column in _COLUMNS:
        op.execute(
            f"ALTER TABLE platform.booking_links "
            f"ADD COLUMN IF NOT EXISTS {column} TIMESTAMP WITH TIME ZONE"
        )


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.execute(f"ALTER TABLE platform.booking_links DROP COLUMN IF EXISTS {column}")
