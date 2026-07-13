# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""inbox quality-review opt-in columns on platform.users

Adds a third, independent per-user consent for quality review of email-reply
drafting (the inbound message plus the AI-drafted reply the clinician then
edits), alongside the existing chat and session-notes consents. Kept separate
because email correspondence is a distinct surface, and a clinician may want to
allow one without the others.

Columns added to ``platform.users``:

- ``inbox_quality_review_opt_in BOOLEAN NOT NULL DEFAULT FALSE`` — current
  consent state. Default OFF; content is only ever captured when this is TRUE.
- ``inbox_quality_review_opt_in_at TIMESTAMPTZ NULL`` — timestamp of the most
  recent opt-in transition.
- ``inbox_quality_review_opt_out_at TIMESTAMPTZ NULL`` — timestamp of the most
  recent opt-out transition; read by the purge path to find users whose
  captured content needs immediate removal.

As with the chat and session-notes columns, these live on the core User model so
any deployment can carry the consent state; the capture pipeline that *reads*
them is wired up by a downstream consumer. Deployments that never wire it up keep
these FALSE/NULL forever, which is correct. The shared
``quality_review_consent_prompted_at`` column already exists (added with the
session-notes consent) and is reused for the onboarding prompt across all scopes.

Revision ID: e6a1c9d4b7f2
Revises: f4b8c2a91e6d
Create Date: 2026-07-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e6a1c9d4b7f2"
down_revision: str | Sequence[str] | None = "f4b8c2a91e6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS inbox_quality_review_opt_in "
        "BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS inbox_quality_review_opt_in_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS inbox_quality_review_opt_out_at TIMESTAMPTZ NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS inbox_quality_review_opt_out_at")
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS inbox_quality_review_opt_in_at")
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS inbox_quality_review_opt_in")
