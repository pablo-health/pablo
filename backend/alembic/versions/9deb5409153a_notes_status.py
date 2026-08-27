# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""notes.status — lifecycle for off-request standalone-note generation

The standalone-note dictation path now persists a note skeleton and
generates its content on a Cloud Tasks worker instead of on the create
request thread. ``status`` tracks that: 'processing' from the moment the
skeleton is persisted, until the worker writes 'complete' (with content)
or 'failed'.

SERVER DEFAULT 'complete': every existing note was written synchronously
with its content already in hand, so every existing row is 'complete'.
Only a note created via the dictation path ever starts 'processing'.

Revision ID: 9deb5409153a
Revises: a91c5d3e7b28
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "9deb5409153a"
down_revision: str | Sequence[str] | None = "a91c5d3e7b28"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE notes ADD COLUMN IF NOT EXISTS status VARCHAR(20) NOT NULL DEFAULT 'complete'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE notes DROP COLUMN IF EXISTS status")
