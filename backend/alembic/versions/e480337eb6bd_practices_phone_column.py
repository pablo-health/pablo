# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices: phone column

A practice had a business address (``address``, written at the
professional-info onboarding step) but nowhere to store a phone
number, even though the Profile settings page needs to show and edit
one alongside the practice name. Adds ``phone`` as a plain nullable
column — no format validation at the DB layer, same posture as
``address``.

Revision ID: e480337eb6bd
Revises: e8c3d17f9042
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e480337eb6bd"
down_revision: str | Sequence[str] | None = "e8c3d17f9042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE platform.practices
            ADD COLUMN IF NOT EXISTS phone VARCHAR(50)
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practices DROP COLUMN IF EXISTS phone")
