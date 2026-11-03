# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_booking_buttons: a practice's "Book a session" link

Where the link on the practice's website and in its portal goes — one of its
booking links, or an outside booking address — and whether each surface shows
it. No row means off.

Idempotent, like the rest of this chain: the platform template already carries
the table on a fresh install.

No PHI: a link id, a public address and two switches.

Revision ID: d3a6f1b8c2e9
Revises: ba0785197edb
Create Date: 2026-11-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3a6f1b8c2e9"
down_revision: str | Sequence[str] | None = "ba0785197edb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_booking_buttons (
            practice_id VARCHAR(128) NOT NULL
                REFERENCES platform.practices (id) ON DELETE CASCADE,
            booking_link_id UUID
                REFERENCES platform.booking_links (id) ON DELETE SET NULL,
            outside_url VARCHAR(2048),
            on_website BOOLEAN NOT NULL DEFAULT false,
            in_portal BOOLEAN NOT NULL DEFAULT false,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_by VARCHAR(128) NOT NULL,
            CONSTRAINT practice_booking_buttons_pkey PRIMARY KEY (practice_id),
            CONSTRAINT ck_practice_booking_buttons_one_target
                CHECK (booking_link_id IS NULL OR outside_url IS NULL)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.practice_booking_buttons;")
