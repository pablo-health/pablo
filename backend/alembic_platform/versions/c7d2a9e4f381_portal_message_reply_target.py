# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_portal_settings: the practice's reply-time target for client messages

``message_reply_target_days`` is how many working days the practice gives
itself to answer a client's message. Every existing row gets the default of
two, the same as a practice that has never set it.

Idempotent, like the rest of this chain: the platform template already
carries the column on a fresh install.

No PHI: a number.

Revision ID: c7d2a9e4f381
Revises: b4e81d6c2f95
Create Date: 2026-10-25
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c7d2a9e4f381"
down_revision: str | Sequence[str] | None = "b4e81d6c2f95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practice_portal_settings "
        "ADD COLUMN IF NOT EXISTS message_reply_target_days SMALLINT NOT NULL DEFAULT 2;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practice_portal_settings "
        "DROP COLUMN IF EXISTS message_reply_target_days;"
    )
