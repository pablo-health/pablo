# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.booking_links.require_email_confirmation

Whether a booking made through this link must clear an email round trip
before it holds a real slot. Born ``true`` for every link, at the
database layer — a fresh row omits the column entirely and Postgres
fills it. There is no API surface, setting, or UI for this column in
this revision; relaxing a link to skip confirmation is a direct
``UPDATE`` an operator runs by hand (docs/design/public-booking.md).

``booking_links`` is platform-scoped, so this migration has no bearing
on the per-tenant schema and needs no tenant template regeneration.

Revision ID: e8c54fe3d577
Revises: d1e7c4a92b58
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e8c54fe3d577"
down_revision: str | Sequence[str] | None = "d1e7c4a92b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.booking_links "
        "ADD COLUMN IF NOT EXISTS require_email_confirmation BOOLEAN NOT NULL DEFAULT true"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE platform.booking_links DROP COLUMN IF EXISTS require_email_confirmation"
    )
