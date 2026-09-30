# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""external_calendar_events — follow sessions another service puts on a calendar

Many clinicians already have their sessions put on their main calendar by
another scheduling service. Pablo can follow them: each such event becomes a
row here, asked about once ("who is this?"), and an answered one becomes an
appointment that follows its event.

* ``external_calendar_events`` — one row per followed event, keyed by
  ``(user_id, source, source_event_id)``. ``answer`` is ``open`` until the
  clinician says who it is, then ``client`` (with ``patient_id`` and the
  ``appointment_id`` made for it) or ``not_a_client``. An open row is not an
  appointment: ``appointments.patient_id`` stays required.
* ``appointments.outside_source`` / ``outside_event_id`` — the event an
  appointment follows, so a move or a deletion there reaches it.
* ``google_calendar_settings.follow_main_calendar`` — the clinician's opt-in,
  kept where it survives a disconnect. ``app_calendar_id`` becomes nullable so
  a clinician who never had Pablo make a calendar can still hold the choice.
* ``google_calendar_tokens.main_calendar_sync_token`` — where the read of the
  main calendar resumes, apart from the token of the calendar Pablo writes.

``external_calendar_events`` carries ``user_id``, so ``enable_rls_on_schema``
gives it the direct-ownership policy: an open row (no patient yet) is its own
clinician's and nobody else's. The tenant migrate job applies it after the
chain runs, and provisioning applies it to new schemas.

The downgrade drops settings rows that hold no calendar before restoring
``NOT NULL``. The table is FORCE-RLS'd and this chain may run as a role
without BYPASSRLS, so that delete runs with row security suspended and
restored — otherwise it would match nothing and ``SET NOT NULL`` would fail on
the rows it could not see.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: a9c3e7d2b518
Revises: f4b2d8a61c73
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a9c3e7d2b518"
down_revision: str | Sequence[str] | None = "f4b2d8a61c73"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS external_calendar_events ("
        "id UUID PRIMARY KEY, "
        "user_id UUID NOT NULL, "
        "source VARCHAR(64) NOT NULL, "
        "source_event_id TEXT NOT NULL, "
        "source_series_id TEXT, "
        "start_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "end_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "title TEXT NOT NULL DEFAULT '', "
        "answer VARCHAR(16) NOT NULL DEFAULT 'open', "
        "patient_id UUID, "
        "appointment_id UUID, "
        "created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
        "updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(), "
        "CONSTRAINT uq_external_calendar_events_event "
        "UNIQUE (user_id, source, source_event_id), "
        "CONSTRAINT ck_external_calendar_events_answer "
        "CHECK (answer IN ('open', 'client', 'not_a_client')))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_external_calendar_events_user_answer "
        "ON external_calendar_events (user_id, answer)"
    )
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS outside_source VARCHAR(64)")
    op.execute("ALTER TABLE appointments ADD COLUMN IF NOT EXISTS outside_event_id TEXT")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointments_outside_event_id "
        "ON appointments (outside_event_id)"
    )
    op.execute(
        "ALTER TABLE google_calendar_settings "
        "ADD COLUMN IF NOT EXISTS follow_main_calendar BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE google_calendar_settings ALTER COLUMN app_calendar_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE google_calendar_tokens ADD COLUMN IF NOT EXISTS main_calendar_sync_token TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE google_calendar_tokens DROP COLUMN IF EXISTS main_calendar_sync_token")
    op.execute(
        """
        DO $$
        DECLARE
            was_enabled boolean;
            was_forced boolean;
        BEGIN
            SELECT relrowsecurity, relforcerowsecurity INTO was_enabled, was_forced
            FROM pg_class WHERE oid = to_regclass('google_calendar_settings');
            ALTER TABLE google_calendar_settings NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE google_calendar_settings DISABLE ROW LEVEL SECURITY;
            DELETE FROM google_calendar_settings WHERE app_calendar_id IS NULL;
            IF was_enabled THEN
                ALTER TABLE google_calendar_settings ENABLE ROW LEVEL SECURITY;
            END IF;
            IF was_forced THEN
                ALTER TABLE google_calendar_settings FORCE ROW LEVEL SECURITY;
            END IF;
            ALTER TABLE google_calendar_settings ALTER COLUMN app_calendar_id SET NOT NULL;
        END $$;
        """
    )
    op.execute("ALTER TABLE google_calendar_settings DROP COLUMN IF EXISTS follow_main_calendar")
    op.execute("DROP INDEX IF EXISTS ix_appointments_outside_event_id")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS outside_event_id")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS outside_source")
    op.execute("DROP TABLE IF EXISTS external_calendar_events")
