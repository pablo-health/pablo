# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments.skipped — a cancelled occurrence the series carries on past

A cancelled row in a recurring series can mean two different things: the
client is not coming that week and the series continues, or something went
wrong with that one session. The calendar draws them differently — a skipped
week is expected and quiet — so the row has to say which it was.

``skipped`` is set together with the cancellation, only on an occurrence of a
series, and cleared when the cancellation is undone. FALSE for every existing
row, which reads as "an ordinary cancellation", exactly what they were.

Revision ID: d2a7c5e19f36
Revises: e7b3d9a2c4f1
Create Date: 2026-10-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d2a7c5e19f36"
down_revision: str | Sequence[str] | None = "e7b3d9a2c4f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS skipped BOOLEAN NOT NULL DEFAULT FALSE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS skipped")
