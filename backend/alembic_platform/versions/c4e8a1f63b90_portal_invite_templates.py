# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.portal_invite_templates: a practice's own portal invitation wording

One row per practice that has changed the wording; no row means the engine's
default. Beside ``companion_practice_slugs`` in the shared schema because it
belongs to the practice, not to a chart.

No PHI: a subject and a plain-text body the practice wrote, with named
placeholders that are filled in only at send time.

**Written idempotently, and expected to be a no-op** on the paths that
materialise the platform tables from the models before this chain runs — the
same reasoning as ``d1c7b94e3a26_portal_practice_slugs``.

Revision ID: c4e8a1f63b90
Revises: a9d3e6f27c15
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4e8a1f63b90"
down_revision: str | Sequence[str] | None = "a9d3e6f27c15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.portal_invite_templates (
            practice_id  VARCHAR(128) NOT NULL PRIMARY KEY,
            subject      VARCHAR(200) NOT NULL,
            body         TEXT         NOT NULL,
            updated_at   TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.portal_invite_templates;")
