# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""google_app_calendars — the calendar Pablo made, remembered past the connection

A connection that writes to a calendar Pablo makes used to find that calendar
again on reconnect from ``google_calendar_tokens.calendar_id``. That column is
the calendar the connection writes to NOW, so connecting to the main calendar
overwrote it, and disconnecting deleted the row it lived on. Either way the
next app-calendar connect had nothing to reuse and made another
"Pablo Sessions" calendar.

This table holds only calendars Pablo created, one per clinician, and nothing
about the grant: a disconnect still deletes the token row outright, and this
one stays.

No backfill here. The ids to copy sit in ``google_calendar_tokens``, which is
FORCE-RLS'd per user, and this chain may run as a role without BYPASSRLS — a
cross-user ``INSERT ... SELECT`` would then copy nothing and say nothing. The
service records an existing app-calendar connection's id instead, from the
clinician's own row, on their next connect or disconnect: the two moments
that would otherwise lose it.

``user_id`` carries the row, so ``enable_rls_on_schema`` gives it the same
direct-ownership policy ``google_calendar_tokens`` has; the tenant migrate job
applies it after the chain runs, and provisioning applies it to new schemas.

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
        "CREATE TABLE IF NOT EXISTS google_app_calendars ("
        "user_id UUID PRIMARY KEY, "
        "calendar_id TEXT NOT NULL, "
        "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
        "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now())"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS google_app_calendars")
