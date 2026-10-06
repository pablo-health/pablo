# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.launch_intents.ai_consent_prompted: off by default

Set when the web's "Start session" asked "No consent on file" and the
clinician chose to record anyway. The desktop app reads it back from the
redeem response so it does not ask the same question a second time.

No PHI: a single flag on a short-lived, single-use hand-off row.

**Written idempotently, and expected to be a no-op** on the paths that
materialise the platform tables from the models before this chain runs — the
same reasoning as ``d1c7b94e3a26_portal_practice_slugs``.

Revision ID: a4d7e2c91b56
Revises: e9a4c2b7d518
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a4d7e2c91b56"
down_revision: str | Sequence[str] | None = "e9a4c2b7d518"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.launch_intents"
        " ADD COLUMN IF NOT EXISTS ai_consent_prompted BOOLEAN NOT NULL DEFAULT false;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.launch_intents DROP COLUMN IF EXISTS ai_consent_prompted;")
