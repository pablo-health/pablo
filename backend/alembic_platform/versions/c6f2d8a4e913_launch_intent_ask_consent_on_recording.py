# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.launch_intents.ask_consent_on_recording: off by default

Set when the web's "Start session" asked "No consent on file" for a telehealth
visit and the clinician chose "Ask now": the desktop app starts recording and
the clinician asks the client then, so the answer is on the recording. The app
reads it back from the redeem response and starts the session saying so.

No PHI: a single flag on a short-lived, single-use hand-off row.

**Written idempotently, and expected to be a no-op** on the paths that
materialise the platform tables from the models before this chain runs — the
same reasoning as ``d1c7b94e3a26_portal_practice_slugs``.

Revision ID: c6f2d8a4e913
Revises: b8e3f5a1c724
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c6f2d8a4e913"
down_revision: str | Sequence[str] | None = "b8e3f5a1c724"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.launch_intents"
        " ADD COLUMN IF NOT EXISTS ask_consent_on_recording BOOLEAN NOT NULL DEFAULT false;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE platform.launch_intents DROP COLUMN IF EXISTS ask_consent_on_recording;"
    )
