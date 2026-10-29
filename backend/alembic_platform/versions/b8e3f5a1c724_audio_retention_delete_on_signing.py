# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.audio_retention_days: allow 0, "delete when the note is signed"

The range was 30..2555 days. It becomes 0..2555: 0 means a session's audio is
deleted once its note is signed, and 1 or more means that many days after the
session, as before. Every existing value is already in the new range, so the
constraint is replaced in place with nothing to backfill.

No PHI: a practice setting.

Revision ID: b8e3f5a1c724
Revises: a4d7e2c91b56
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b8e3f5a1c724"
down_revision: str | Sequence[str] | None = "a4d7e2c91b56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_practices_audio_retention_days_range"


def _replace_range(low: int) -> None:
    op.execute(f"ALTER TABLE platform.practices DROP CONSTRAINT IF EXISTS {_CONSTRAINT};")
    op.execute(
        f"ALTER TABLE platform.practices ADD CONSTRAINT {_CONSTRAINT} "
        f"CHECK (audio_retention_days >= {low} AND audio_retention_days <= 2555);"
    )


def upgrade() -> None:
    _replace_range(0)


def downgrade() -> None:
    # A practice set to delete on signing has no 30-day equivalent; the
    # shortest the old range allowed is the closest it can go back to.
    op.execute(
        "UPDATE platform.practices SET audio_retention_days = 30 WHERE audio_retention_days < 30;"
    )
    _replace_range(30)
