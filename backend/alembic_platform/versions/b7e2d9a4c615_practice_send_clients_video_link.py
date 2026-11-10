# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.send_clients_video_link: on by default

Whether messages to the practice's clients carry the appointment's video link.
Every practice starts with it on, existing ones included: the column default
fills every current row, so there is no separate backfill.

No PHI: a single practice preference.

Idempotent, like the rest of this chain: the platform template already carries
the column on a fresh install.

Revision ID: b7e2d9a4c615
Revises: d3a6f1b8c2e9
Create Date: 2026-11-10
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b7e2d9a4c615"
down_revision: str | Sequence[str] | None = "d3a6f1b8c2e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practices"
        " ADD COLUMN IF NOT EXISTS send_clients_video_link BOOLEAN NOT NULL DEFAULT true;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS send_clients_video_link;")
