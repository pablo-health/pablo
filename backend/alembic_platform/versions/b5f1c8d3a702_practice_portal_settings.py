# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practice_portal_settings: whether a practice offers the portal

One row per practice that has answered the question. No row means the portal
is off: from here on a practice turns it on rather than finding it on.

**Every practice that exists when this runs gets a row with the portal on.**
Until now the portal was on for every practice by default, so turning it off
for the ones already here would take it away from clients who may already be
using it. ``decided_at`` is set on those rows too, so nobody who already had
the portal is asked whether they want it.

The CREATE is idempotent for the same reason as
``d1c7b94e3a26_portal_practice_slugs``: the platform tables are materialised
from the models before this chain runs. The INSERT is not a no-op on those
paths, and ``ON CONFLICT DO NOTHING`` makes it safe to run again.

No PHI: a switch, module names and timestamps.

Revision ID: b5f1c8d3a702
Revises: e7b3c52d9a14
Create Date: 2026-09-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b5f1c8d3a702"
down_revision: str | Sequence[str] | None = "e7b3c52d9a14"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS platform.practice_portal_settings (
            practice_id      VARCHAR(128) NOT NULL PRIMARY KEY,
            enabled          BOOLEAN      NOT NULL DEFAULT false,
            enabled_modules  TEXT[],
            decided_at       TIMESTAMP WITH TIME ZONE,
            updated_at       TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_by       VARCHAR(128)
        );
        """
    )
    op.execute(
        """
        INSERT INTO platform.practice_portal_settings
            (practice_id, enabled, enabled_modules, decided_at, updated_at, updated_by)
        SELECT id, true, NULL, now(), now(), NULL
        FROM platform.practices
        WHERE deleted_at IS NULL
        ON CONFLICT (practice_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS platform.practice_portal_settings;")
