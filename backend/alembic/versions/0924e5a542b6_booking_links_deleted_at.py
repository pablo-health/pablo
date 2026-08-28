# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.booking_links.deleted_at

Soft-delete for booking links. Deleting a link stamps this column
(and clears ``is_active``) instead of removing the row, so the
existing ``UNIQUE(slug)`` constraint keeps the slug claimed forever --
nobody, including the original owner, can re-register it. NULL means
live; every read path treats a non-NULL row as absent.

``booking_links`` is platform-scoped, so this migration has no bearing
on the per-tenant schema and needs no tenant template regeneration.

Revision ID: 0924e5a542b6
Revises: f06ce10d60c7
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0924e5a542b6"
down_revision: str | Sequence[str] | None = "f06ce10d60c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform.booking_links ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")


def downgrade() -> None:
    op.execute("ALTER TABLE platform.booking_links DROP COLUMN IF EXISTS deleted_at")
