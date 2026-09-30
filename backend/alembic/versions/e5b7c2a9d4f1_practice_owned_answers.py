# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Remembered answers belong to the practice; one outside event books once

Additive only: nullable columns, indexes, a policy and a function. Nothing
is dropped and no row changes meaning, so an image from before this revision
keeps working against the migrated schema while a deploy rolls over.

``patient_source_mappings``:

* ``scope`` — whose answer a row is: ``practice`` for a feed's client codes,
  ``calendar:<id>`` for a calendar's series. NULL on every row from before
  this revision, which is how the app tells them apart.
* ``answered_by_user_id`` — who answered; a fact about the answer, not its
  key. ``session_clinician_user_id`` — whose session a calendar's answer
  books for, for a later release to read.
* A unique index on ``(scope, source, source_identifier)`` over scoped rows:
  the practice's key, and what lets writes be insert-on-conflict.
* The row policy: a scoped row is readable by any armed clinician in the
  practice, a row without a scope only by its owner. Rows from before this
  revision hold the identifier in plain text, so they stay their owner's
  until the app adopts them. Adoption is the app's job, not this revision's:
  the new rows hold a keyed digest of the identifier, and the key is an
  application secret a migration cannot reach. ``user_id`` stays required
  and the app fills it with the answerer; a later release drops it.

``appointments``: one outside event is at most one live appointment in the
practice. Two clinicians following one shared calendar must never book the
same session twice, so a unique partial index on
``(outside_source, outside_calendar_id, outside_event_id)`` over live rows
guards it. A feed is one clinician's, and sessions booked before calendars
were recorded have no calendar, so a second index keeps those unique per
clinician on ``(outside_source, user_id, outside_event_id)``.

**Rows that already break that rule.** Before the index goes on, any group
of live appointments sharing a key is reduced to one: the oldest keeps
following the event, and the others stop following it — their three
``outside_*`` columns are cleared, and nothing else about them changes.
No session is cancelled and none is lost; a duplicate simply stops being
tied to the event, which two rows never should have been. The open rows
pointing at a duplicate are re-pointed at the one kept, so the practice's
view of that event is one appointment. Both tables are FORCE-RLS'd and this
chain may run as a role without BYPASSRLS, so that pass runs with row
security suspended and restored — otherwise it would see nothing.

``practice_outside_appointment(source, calendar_id, event_id)`` — the live
appointment following one outside event, wherever in the practice it is,
so a second follower can link to it. Created here and so captured by the
template; owned, granted and policied by
``app.db.practice_directory.apply_practice_directory_access``, like the
client directory beside it. A re-walk over a schema whose function the
directory role already owns leaves it alone; downgrade drops it as its
owner.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema, and each step is guarded.

Revision ID: e5b7c2a9d4f1
Revises: d6a2e9f4b1c8
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.engine import Connection

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "e5b7c2a9d4f1"
down_revision: str | Sequence[str] | None = "d6a2e9f4b1c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every live appointment following an outside event, with the one its group
#: keeps: the oldest, by creation then id. A group is one event on one
#: calendar, or one event on one clinician's feed.
_DUPLICATES = """
    WITH duplicates AS (
        SELECT id,
               first_value(id) OVER (
                   PARTITION BY outside_source,
                                coalesce(outside_calendar_id, ''),
                                CASE WHEN outside_calendar_id IS NULL THEN user_id END,
                                outside_event_id
                   ORDER BY created_at NULLS LAST, id
               ) AS kept_id
        FROM appointments
        WHERE outside_source IS NOT NULL
          AND outside_event_id IS NOT NULL
          AND status <> 'cancelled'
    )
"""

_ONE_APPOINTMENT_PER_OUTSIDE_EVENT = """
DO $$
DECLARE
    appointments_enabled boolean;
    appointments_forced boolean;
    rows_enabled boolean;
    rows_forced boolean;
BEGIN
    SELECT relrowsecurity, relforcerowsecurity INTO appointments_enabled, appointments_forced
    FROM pg_class WHERE oid = to_regclass('appointments');
    SELECT relrowsecurity, relforcerowsecurity INTO rows_enabled, rows_forced
    FROM pg_class WHERE oid = to_regclass('external_calendar_events');
    ALTER TABLE appointments NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE appointments DISABLE ROW LEVEL SECURITY;
    ALTER TABLE external_calendar_events NO FORCE ROW LEVEL SECURITY;
    ALTER TABLE external_calendar_events DISABLE ROW LEVEL SECURITY;

    __DUPLICATES__
    UPDATE external_calendar_events e
       SET appointment_id = d.kept_id
      FROM duplicates d
     WHERE e.appointment_id = d.id AND d.id <> d.kept_id;
    __DUPLICATES__
    UPDATE appointments a
       SET outside_source = NULL, outside_event_id = NULL, outside_calendar_id = NULL
      FROM duplicates d
     WHERE a.id = d.id AND d.id <> d.kept_id;

    IF appointments_enabled THEN
        ALTER TABLE appointments ENABLE ROW LEVEL SECURITY;
    END IF;
    IF appointments_forced THEN
        ALTER TABLE appointments FORCE ROW LEVEL SECURITY;
    END IF;
    IF rows_enabled THEN
        ALTER TABLE external_calendar_events ENABLE ROW LEVEL SECURITY;
    END IF;
    IF rows_forced THEN
        ALTER TABLE external_calendar_events FORCE ROW LEVEL SECURITY;
    END IF;
END $$;
""".replace("__DUPLICATES__", _DUPLICATES)


def _schema(bind: Connection) -> str:
    """The schema this pass is migrating. A tenant revision always runs in one."""
    from app.db import _validate_schema_name  # noqa: PLC0415

    schema = bind.execute(text("SELECT current_schema()")).scalar()
    if not schema:
        msg = "practice_owned_answers: no current schema to migrate"
        raise RuntimeError(msg)
    _validate_schema_name(schema)
    return str(schema)


def upgrade() -> None:
    # Imported here, not at module level: revision walkers import every
    # migration without env.py's sys.path setup (see e2b7c4f19d38).
    from app.db import DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415
    from app.db.practice_answers import apply_practice_answers_policy  # noqa: PLC0415
    from app.db.practice_directory import (  # noqa: PLC0415
        apply_practice_directory_access,
        create_outside_appointment_function,
    )

    bind = op.get_bind()
    schema = _schema(bind)

    op.execute(
        """
        ALTER TABLE patient_source_mappings ADD COLUMN IF NOT EXISTS scope TEXT;
        ALTER TABLE patient_source_mappings ADD COLUMN IF NOT EXISTS answered_by_user_id UUID;
        ALTER TABLE patient_source_mappings
            ADD COLUMN IF NOT EXISTS session_clinician_user_id UUID;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_patient_source_mappings_scoped
            ON patient_source_mappings (scope, source, source_identifier)
            WHERE scope IS NOT NULL;
        """
    )

    op.execute(_ONE_APPOINTMENT_PER_OUTSIDE_EVENT)
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_outside_event_per_calendar
            ON appointments (outside_source, outside_calendar_id, outside_event_id)
            WHERE status <> 'cancelled' AND outside_calendar_id IS NOT NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS uq_appointments_outside_event_per_clinician
            ON appointments (outside_source, user_id, outside_event_id)
            WHERE status <> 'cancelled' AND outside_calendar_id IS NULL
              AND outside_event_id IS NOT NULL;
        """
    )

    create_outside_appointment_function(bind, schema)
    if schema != DEFAULT_PRACTICE_SCHEMA:
        # The template schema carries no policies: ``enable_rls_on_schema``
        # skips it, and nothing reads it.
        apply_practice_answers_policy(bind, schema)
        apply_practice_directory_access(bind, schema)


def downgrade() -> None:
    from app.db import DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415
    from app.db.practice_answers import restore_owner_policy  # noqa: PLC0415
    from app.db.practice_directory import drop_outside_appointment_function  # noqa: PLC0415

    bind = op.get_bind()
    schema = _schema(bind)

    op.execute("DROP POLICY IF EXISTS rls_practice_directory_read ON appointments")
    drop_outside_appointment_function(bind, schema)
    op.execute(
        """
        DROP INDEX IF EXISTS uq_appointments_outside_event_per_clinician;
        DROP INDEX IF EXISTS uq_appointments_outside_event_per_calendar;
        DROP INDEX IF EXISTS uq_patient_source_mappings_scoped;
        """
    )
    if schema != DEFAULT_PRACTICE_SCHEMA:
        restore_owner_policy(bind, schema)
    # The rows the practice answered stay, under the answerer's ``user_id``:
    # an older image reads them as that clinician's and never matches their
    # digests, which is harmless, and nothing answered is thrown away.
    op.execute(
        """
        ALTER TABLE patient_source_mappings DROP COLUMN IF EXISTS session_clinician_user_id;
        ALTER TABLE patient_source_mappings DROP COLUMN IF EXISTS answered_by_user_id;
        ALTER TABLE patient_source_mappings DROP COLUMN IF EXISTS scope;
        """
    )
