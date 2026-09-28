# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.portal_welcome_messages: a practice's own portal welcome

One row per practice that has changed the welcome on the portal's home screen;
no row means the engine's default. Beside ``portal_invite_templates`` in the
shared schema because it belongs to the practice, not to a chart.

No PHI: a heading and a plain-text body the practice wrote, with one
placeholder — the practice's own name — filled in when the portal reads it.

**Written idempotently, and expected to be a no-op** on the paths that
materialise the platform tables from the models before this chain runs — the
same reasoning as ``d1c7b94e3a26_portal_practice_slugs``.

Revision ID: e7b3c52d9a14
Revises: c4e8a1f63b90
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e7b3c52d9a14"
down_revision: str | Sequence[str] | None = "c4e8a1f63b90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.portal_welcome_messages (
            practice_id  VARCHAR(128) NOT NULL PRIMARY KEY,
            heading      TEXT         NOT NULL,
            body         TEXT         NOT NULL,
            updated_at   TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.portal_welcome_messages;")
