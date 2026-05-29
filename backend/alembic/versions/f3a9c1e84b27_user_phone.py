# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""phone column on platform.users

Adds ``phone VARCHAR(50) NULL`` to ``platform.users``.

Optional contact number, collected during onboarding. May be used for
account recovery or support purposes; it is never a sole authentication
factor. NULL means "not provided" — the column is purely additive and
deployments that don't surface a phone field simply leave it NULL.

Revision ID: f3a9c1e84b27
Revises: a4f7e2c81b9d
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f3a9c1e84b27"
down_revision: str | Sequence[str] | None = "a4f7e2c81b9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE platform.users ADD COLUMN IF NOT EXISTS phone VARCHAR(50) NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS phone")
