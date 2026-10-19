# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_sites.served_hosted_host

The hosted website address (``<slug>.<hosted domain>``) the domain reconciler
routed at the served version, where the deployment serves websites from object
storage. Kept so a slug change, or the hosted domain going away, takes that
host rule down again. ``NULL`` on every existing row: none is routed yet.

Idempotent, like the rest of this chain: the platform template already
carries the column on a fresh install.

No PHI: a hostname of a practice's public website.

Revision ID: f2d9c7b3a641
Revises: e7c4b2a9d518
Create Date: 2026-10-19
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f2d9c7b3a641"
down_revision: str | Sequence[str] | None = "e7c4b2a9d518"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practice_sites "
        "ADD COLUMN IF NOT EXISTS served_hosted_host VARCHAR(253)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practice_sites DROP COLUMN IF EXISTS served_hosted_host")
