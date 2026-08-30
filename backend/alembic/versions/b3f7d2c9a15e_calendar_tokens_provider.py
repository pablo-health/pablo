# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""google_calendar_tokens.provider — which calendar issued the stored tokens

The calendar integration is no longer written against one provider's API,
so a token row has to say whose tokens it holds before a second provider
can ever be connected.

Every row that exists today is Google's, and the column carries a
``google`` server default so it stays that way without a backfill and
without asking a single therapist to re-consent. The default is also what
a row written by code that predates this column would get.

Deliberately an unconstrained VARCHAR rather than an enum: adding a
provider should not need a schema migration before its first row can be
written.

``google_calendar_tokens`` is per-tenant, so this runs once per schema
through the tenant fan-out and the tenant template must be regenerated.

Revision ID: b3f7d2c9a15e
Revises: c7a1f4e08b93
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b3f7d2c9a15e"
down_revision: str | Sequence[str] | None = "c7a1f4e08b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE google_calendar_tokens "
        "ADD COLUMN IF NOT EXISTS provider VARCHAR(32) NOT NULL DEFAULT 'google'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE google_calendar_tokens DROP COLUMN IF EXISTS provider")
