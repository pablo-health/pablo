# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Follow a calendar the clinician chooses, and record which calendar a session is on

Additive only: three nullable columns and a backfill. Nothing is dropped, so an
image from before this revision keeps working against the migrated schema
while a deploy rolls over.

* ``google_calendar_settings.follow_calendar_id`` — the calendar whose sessions
  from another service are followed, or NULL. Replaces the on/off
  ``follow_main_calendar``: every row following the main calendar gets
  ``'primary'``, which the next read resolves to the calendar's real id.
  ``follow_main_calendar`` stays in place and is no longer read; a later
  revision drops it. It is still written, as "following the main calendar":
  an image from before this revision reads only that calendar, so it follows
  for a clinician on the main calendar and not at all for one who chose
  another, whose sessions it would otherwise judge against the wrong calendar.
* ``external_calendar_events.calendar_id`` — the followed calendar an event is
  on. NULL for a feed.
* ``appointments.outside_calendar_id`` — the followed calendar the event an
  appointment follows is on. NULL for a feed.

Rows and appointments made before this revision have no calendar recorded.
They all came from the main calendar, the only one that could be followed, and
the first time a read learns the main calendar's real id it records it on them.

The settings table is FORCE-RLS'd and this chain may run as a role without
BYPASSRLS, so the backfill runs with row security suspended and restored —
otherwise it would match nothing.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema, and each step is guarded.

Revision ID: d6a2e9f4b1c8
Revises: c3f7a1d9e2b4
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d6a2e9f4b1c8"
down_revision: str | Sequence[str] | None = "c3f7a1d9e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE google_calendar_settings ADD COLUMN IF NOT EXISTS follow_calendar_id TEXT;
        ALTER TABLE external_calendar_events ADD COLUMN IF NOT EXISTS calendar_id TEXT;
        ALTER TABLE appointments ADD COLUMN IF NOT EXISTS outside_calendar_id TEXT;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            was_enabled boolean;
            was_forced boolean;
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'google_calendar_settings'
                  AND column_name = 'follow_main_calendar'
            ) THEN
                RETURN;
            END IF;
            SELECT relrowsecurity, relforcerowsecurity INTO was_enabled, was_forced
            FROM pg_class WHERE oid = to_regclass('google_calendar_settings');
            ALTER TABLE google_calendar_settings NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE google_calendar_settings DISABLE ROW LEVEL SECURITY;
            UPDATE google_calendar_settings
               SET follow_calendar_id = 'primary'
             WHERE follow_main_calendar AND follow_calendar_id IS NULL;
            IF was_enabled THEN
                ALTER TABLE google_calendar_settings ENABLE ROW LEVEL SECURITY;
            END IF;
            IF was_forced THEN
                ALTER TABLE google_calendar_settings FORCE ROW LEVEL SECURITY;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE appointments DROP COLUMN IF EXISTS outside_calendar_id;
        ALTER TABLE external_calendar_events DROP COLUMN IF EXISTS calendar_id;
        ALTER TABLE google_calendar_settings DROP COLUMN IF EXISTS follow_calendar_id;
        """
    )
