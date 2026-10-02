# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_sites.draft_theme and platform.practice_site_versions.theme

A website can carry a ``theme.json`` its portal takes colors and fonts from
(``app.sites.theme``). ``draft_theme`` is what the draft's file gave and what it
skipped, shown on the Website page; ``theme`` is the theme a published version
gives the portal, kept with the version so rolling back brings it back too.

Existing rows start with both empty: a website published before this has no
theme, which is how it already looks.

No PHI: a practice's public colors and fonts.

Revision ID: b4e7a2c95d18
Revises: e3b8c41f6a52
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b4e7a2c95d18"
down_revision: str | Sequence[str] | None = "e3b8c41f6a52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practice_sites ADD COLUMN IF NOT EXISTS draft_theme JSONB;
        ALTER TABLE platform.practice_site_versions ADD COLUMN IF NOT EXISTS theme JSONB;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practice_site_versions DROP COLUMN IF EXISTS theme;
        ALTER TABLE platform.practice_sites DROP COLUMN IF EXISTS draft_theme;
        """
    )
