# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Record every Google calendar Pablo creates for a clinician

Additive only: one new table and a backfill. An image from before this
revision never reads it, so it keeps working against the migrated schema while
a deploy rolls over.

* ``google_created_calendars`` — one row per calendar Pablo's own insert
  created on a clinician's Google account, kept when Pablo moves on to a new
  one (the old calendar was deleted, or the clinician connected another
  account). ``google_calendar_settings.app_calendar_id`` still names the one in
  use; this is the history behind it, so a calendar Pablo made before can be
  recognised as Pablo's when the clinician later chooses which calendar to
  bring sessions in from. ``marked_at`` is when Pablo's marker was written
  into the calendar's description on Google, or NULL until it has been.

The backfill records every calendar already known to be Pablo's: the
remembered ``app_calendar_id``, and the write calendar of any connection that
writes to a calendar Pablo made. Neither is marked yet; the next connect or
read of that connection marks it.

Both source tables are FORCE-RLS'd and this chain may run as a role without
BYPASSRLS, so the backfill runs with row security suspended and restored —
otherwise it would match nothing. The new table is given its row policy by the
reconcile pass that follows every migration (it has a ``user_id`` column).

Idempotent, like every revision in this chain: it is fanned out once per
practice schema, and each step is guarded.

Revision ID: c7d2a9e4f1b6
Revises: b8e3f1c6d2a5
Create Date: 2026-10-04
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c7d2a9e4f1b6"
down_revision: str | Sequence[str] | None = "b8e3f1c6d2a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS google_created_calendars (
            user_id UUID NOT NULL,
            calendar_id TEXT NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            marked_at TIMESTAMP WITH TIME ZONE,
            CONSTRAINT google_created_calendars_pkey PRIMARY KEY (user_id, calendar_id)
        );
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            settings_enabled boolean;
            settings_forced boolean;
            tokens_enabled boolean;
            tokens_forced boolean;
        BEGIN
            SELECT relrowsecurity, relforcerowsecurity INTO settings_enabled, settings_forced
            FROM pg_class WHERE oid = to_regclass('google_calendar_settings');
            SELECT relrowsecurity, relforcerowsecurity INTO tokens_enabled, tokens_forced
            FROM pg_class WHERE oid = to_regclass('google_calendar_tokens');
            ALTER TABLE google_calendar_settings NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE google_calendar_settings DISABLE ROW LEVEL SECURITY;
            ALTER TABLE google_calendar_tokens NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE google_calendar_tokens DISABLE ROW LEVEL SECURITY;

            INSERT INTO google_created_calendars (user_id, calendar_id, created_at)
            SELECT user_id, app_calendar_id, created_at
              FROM google_calendar_settings
             WHERE app_calendar_id IS NOT NULL AND app_calendar_id <> ''
            ON CONFLICT DO NOTHING;

            INSERT INTO google_created_calendars (user_id, calendar_id, created_at)
            SELECT user_id, calendar_id, COALESCE(connected_at, now())
              FROM google_calendar_tokens
             WHERE write_target = 'app_calendar'
               AND calendar_id IS NOT NULL AND calendar_id <> ''
            ON CONFLICT DO NOTHING;

            IF settings_enabled THEN
                ALTER TABLE google_calendar_settings ENABLE ROW LEVEL SECURITY;
            END IF;
            IF settings_forced THEN
                ALTER TABLE google_calendar_settings FORCE ROW LEVEL SECURITY;
            END IF;
            IF tokens_enabled THEN
                ALTER TABLE google_calendar_tokens ENABLE ROW LEVEL SECURITY;
            END IF;
            IF tokens_forced THEN
                ALTER TABLE google_calendar_tokens FORCE ROW LEVEL SECURITY;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS google_created_calendars;")
