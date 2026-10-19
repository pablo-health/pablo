# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_sites.hide_from_search

Whether the practice asks search engines not to list its website. On, every
answer on the website's hosts says ``noindex, nofollow`` and its
``robots.txt`` disallows everything. Every existing row starts off, as a
website always has been.

Idempotent, like the rest of this chain: the platform template already
carries the column on a fresh install.

No PHI: a yes or no about a practice's public website.

Revision ID: d6b2f9e4a713
Revises: a8e5d3c7f214
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d6b2f9e4a713"
down_revision: str | Sequence[str] | None = "a8e5d3c7f214"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practice_sites "
        "ADD COLUMN IF NOT EXISTS hide_from_search BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practice_sites DROP COLUMN IF EXISTS hide_from_search")
