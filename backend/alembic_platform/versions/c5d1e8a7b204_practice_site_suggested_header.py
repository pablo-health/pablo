# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_sites.draft_suggested_header

A portal header suggested from a draft website's ``index.html``
(``app.sites.suggest``), for a draft whose ``theme.json`` declares none. Shown
on the Website page to accept or edit; never part of a published version.

Existing rows start empty: a draft saved before this has no suggestion.

No PHI: a practice's public website header.

Revision ID: c5d1e8a7b204
Revises: b7e2c4f9a031
Create Date: 2026-10-03
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c5d1e8a7b204"
down_revision: str | Sequence[str] | None = "b7e2c4f9a031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practice_sites ADD COLUMN IF NOT EXISTS draft_suggested_header JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practice_sites DROP COLUMN IF EXISTS draft_suggested_header")
