# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""chat quality review opt-in columns on platform.users (THERAPY-8biz)

Adds three columns to ``platform.users``:

- ``chat_quality_review_opt_in BOOLEAN NOT NULL DEFAULT FALSE`` — current
  consent state. Default OFF matches the Path A "no unsolicited PHI in
  logs" discipline; capture pipeline only emits prompt/response text
  when this is TRUE.
- ``chat_quality_review_opt_in_at TIMESTAMPTZ NULL`` — timestamp of the
  most recent opt-in transition. Survives subsequent opt-outs so the
  user-visible consent history is reconstructable from the row + audit
  log together.
- ``chat_quality_review_opt_out_at TIMESTAMPTZ NULL`` — timestamp of the
  most recent opt-out transition. Read by the purge cron
  (THERAPY-c4v5) to identify users whose captured content needs
  immediate removal independent of the rolling 30-day retention.

The column lives in OSS because the user row is OSS; the capture
pipeline that *reads* it is SaaS-only (Langfuse self-host lives in
SaaS infra). Self-hosted OSS installs that never wire up Langfuse will
keep this column FALSE forever, which is correct.

Revision ID: f9b2e7d1a4c8
Revises: e7c4f1a8d293
Create Date: 2026-05-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f9b2e7d1a4c8"
down_revision: str | Sequence[str] | None = "e7c4f1a8d293"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS chat_quality_review_opt_in BOOLEAN NOT NULL DEFAULT FALSE"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS chat_quality_review_opt_in_at TIMESTAMPTZ NULL"
    )
    op.execute(
        "ALTER TABLE platform.users "
        "ADD COLUMN IF NOT EXISTS chat_quality_review_opt_out_at TIMESTAMPTZ NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS chat_quality_review_opt_out_at")
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS chat_quality_review_opt_in_at")
    op.execute("ALTER TABLE platform.users DROP COLUMN IF EXISTS chat_quality_review_opt_in")
