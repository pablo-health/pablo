# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""google_calendar_tokens.app_calendar_id — the calendar Pablo made, remembered

A connection that writes to a calendar Pablo makes finds that calendar again
on reconnect from its own token record. ``calendar_id`` could not carry that:
it is the calendar the connection writes to NOW, so choosing the main
calendar overwrote it, and disconnecting deleted the row it lived on. Either
way the next app-calendar connect had nothing to reuse and made another
"Pablo Sessions" calendar.

``app_calendar_id`` holds only the id of a calendar Pablo created, and it
survives both a switch of write target and a disconnect. Existing app-calendar
connections are backfilled from ``calendar_id``, which for them is exactly
that id.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: c8a2e5f19d34
Revises: d3a7f1c9e254
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c8a2e5f19d34"
down_revision: str | Sequence[str] | None = "d3a7f1c9e254"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE google_calendar_tokens ADD COLUMN IF NOT EXISTS app_calendar_id VARCHAR(255)"
    )
    op.execute(
        "UPDATE google_calendar_tokens SET app_calendar_id = calendar_id "
        "WHERE write_target = 'app_calendar' AND app_calendar_id IS NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE google_calendar_tokens DROP COLUMN IF EXISTS app_calendar_id")
