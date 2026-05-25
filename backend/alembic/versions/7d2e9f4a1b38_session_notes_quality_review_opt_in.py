# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""session-notes quality-review opt-in columns on platform.users

Adds a second, independent per-user consent for quality review of
session-derived notes (the session transcript plus generated note text),
alongside the existing chat consent. Kept separate because session-derived
content is a distinct surface from chat, and a clinician may want to allow
one without the other.

Columns added to ``platform.users``:

- ``session_notes_quality_review_opt_in BOOLEAN NOT NULL DEFAULT FALSE`` —
  current consent state. Default OFF; content is only ever captured when
  this is TRUE.
- ``session_notes_quality_review_opt_in_at TIMESTAMPTZ NULL`` — timestamp
  of the most recent opt-in transition.
- ``session_notes_quality_review_opt_out_at TIMESTAMPTZ NULL`` — timestamp
  of the most recent opt-out transition; read by the purge path to find
  users whose captured content needs immediate removal.
- ``quality_review_consent_prompted_at TIMESTAMPTZ NULL`` — when the user
  was shown (and answered) the optional consent step during onboarding, so
  the wizard does not re-prompt once answered. This is independent of the
  opt-in flags: a user who declined still has it set.

As with the chat columns, these live on the core User model so any
deployment can carry the consent state; the capture pipeline that *reads*
them is wired up by a downstream consumer. Deployments that never wire it
up keep these FALSE/NULL forever, which is correct.

Revision ID: 7d2e9f4a1b38
Revises: c5f3a8e72d91
Create Date: 2026-05-25
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "7d2e9f4a1b38"
down_revision: str | Sequence[str] | None = "c5f3a8e72d91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS session_notes_quality_review_opt_in "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS session_notes_quality_review_opt_in_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS session_notes_quality_review_opt_out_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS quality_review_consent_prompted_at TIMESTAMPTZ NULL"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users DROP COLUMN IF EXISTS quality_review_consent_prompted_at"
    )
    op.execute(
        "ALTER TABLE platform.users DROP COLUMN IF EXISTS session_notes_quality_review_opt_out_at"
    )
    op.execute(
        "ALTER TABLE platform.users DROP COLUMN IF EXISTS session_notes_quality_review_opt_in_at"
    )
    op.execute(
        "ALTER TABLE platform.users DROP COLUMN IF EXISTS session_notes_quality_review_opt_in"
    )
