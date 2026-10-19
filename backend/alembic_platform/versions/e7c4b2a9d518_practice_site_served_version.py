# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_sites.served_version, served_hide_from_search

What the load balancer serves for a website's hosts, where the deployment
serves published websites from object storage (``app.sites.mirror``): the
version copied to the public bucket and routed to, and the ``hide_from_search``
its ``robots.txt`` reflects. The domain reconciler writes both once they are
in place; a publish, a roll back or a search change leaves them behind
``live_version`` / ``hide_from_search``, which is what asks it for a run.
``NULL`` on every existing row: nothing has been served that way yet.

Idempotent, like the rest of this chain: the platform template already
carries the columns on a fresh install.

No PHI: a version number and a yes or no about a practice's public website.

Revision ID: e7c4b2a9d518
Revises: d6b2f9e4a713
Create Date: 2026-10-19
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e7c4b2a9d518"
down_revision: str | Sequence[str] | None = "d6b2f9e4a713"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.practice_sites ADD COLUMN IF NOT EXISTS served_version INTEGER"
    )
    op.execute(
        "ALTER TABLE platform.practice_sites "
        "ADD COLUMN IF NOT EXISTS served_hide_from_search BOOLEAN"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.practice_sites DROP COLUMN IF EXISTS served_version")
    op.execute("ALTER TABLE platform.practice_sites DROP COLUMN IF EXISTS served_hide_from_search")
