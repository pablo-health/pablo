# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rows naming a client go with the client: patient_id cascades on seven more tables

Seven per-tenant tables name a client with a ``patient_id`` uuid and have no
foreign key to ``patients``, so deleting a client's ``patients`` row never
reached them. The hard purge papered over that with a hand-kept list of
deletes (``hard_purge_cron._PATIENT_ROW_TABLES``); the next table with a
``patient_id`` column and no key would have quietly kept a purged client's
rows. This revision makes the database do it:
``FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE`` on
every table that had none, named the pg_dump default
``<table>_patient_id_fkey`` as ``e7c4b9a25f18`` did.

``tasks.patient_id`` is not touched. It stays ``ON DELETE SET NULL``: a
compliance task linked to a client is the clinician's own record and is kept,
unlinked. ``audit_logs.patient_id`` stays a bare identifier: a forensic log
outlives what it names.

Each key is added in one step, validated against every existing row at once.
No rows are deleted. That is plain because no deployment yet holds the rows
the old purge could have left behind; earlier drafts of this revision deleted
orphans first, which was machinery with nothing to clean.

If a row does name a missing client, the migration stops on that table, names
it with the remedy, and deletes nothing: the tenant's upgrade rolls back
whole, the row included.

ROW-LEVEL SECURITY. A key's validation is an ordinary query run as the
migration role, which has no BYPASSRLS and arms no principal, so under FORCE
it sees none of ``patients`` and none of any force-RLS child. Left alone, that
breaks both ways: a child without row security (``companion_sessions``)
looks entirely orphaned and fails a healthy tenant, and a force-RLS child
looks empty and passes with a real orphan in it, its key marked valid. So
FORCE alone is lifted on ``patients`` and the seven tables for the adds (the
owner then reads past its own policies) and put back on exactly the tables
that had it, inside the same block. Policies and ENABLE are never touched;
on failure the rollback restores FORCE with everything else.

``external_calendar_events`` gets an index on ``patient_id``: it had none, and
without one every client deleted would scan the table for rows to cascade to.

Idempotent under the per-tenant fan-out: each add is guarded on the constraint
not existing and the index on ``IF NOT EXISTS``; a re-run lifts and restores
FORCE with nothing to add. The downgrade drops the keys
and the index.

Revision ID: 8c5e65a0e56a
Revises: 16d6543b3c6f
Create Date: 2026-11-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "8c5e65a0e56a"
down_revision: str | Sequence[str] | None = "16d6543b3c6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Literals, not derived from the live template or models: a migration
#: describes the schema at one moment. ``test_patient_id_foreign_keys``
#: re-derives the set from the template and fails if a column is left out.
_TABLES: tuple[str, ...] = (
    "patient_intake_assignments",
    "patient_intake_submissions",
    "patient_charges",
    "patient_payment_methods",
    "companion_sessions",
    "companion_auth_challenges",
    "external_calendar_events",
)

_INDEX = "ix_external_calendar_events_patient_id"


def _fk_name(table: str) -> str:
    return f"{table}_patient_id_fkey"


def _add_key(table: str) -> str:
    """PL/pgSQL adding the validated key unless it exists; an orphan fails naming the table."""
    name = _fk_name(table)
    return f"""
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint con
                JOIN pg_class c ON con.conrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = current_schema()
                  AND c.relname = '{table}'
                  AND con.conname = '{name}'
            ) THEN
                BEGIN
                    EXECUTE format(
                        'ALTER TABLE %I.{table} ADD CONSTRAINT {name} '
                        'FOREIGN KEY (patient_id) '
                        'REFERENCES %I.patients(id) ON DELETE CASCADE',
                        current_schema(), current_schema()
                    );
                EXCEPTION WHEN foreign_key_violation THEN
                    RAISE EXCEPTION
                        '%.{table} has rows whose patient_id names no client; '
                        'delete those rows (or restore the clients) and re-run '
                        'the migration', current_schema()
                        USING ERRCODE = 'foreign_key_violation', DETAIL = SQLERRM;
                END;
            END IF;"""  # noqa: S608 -- all identifiers are module-level constants


def upgrade() -> None:
    lifted = ", ".join(f"'{t}'" for t in ("patients", *_TABLES))
    adds = "".join(_add_key(table) for table in _TABLES)
    op.execute(
        f"""DO $$
        DECLARE
            forced text[];
            t text;
        BEGIN
            SELECT coalesce(array_agg(c.relname), '{{}}') INTO forced
            FROM pg_class c JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname IN ({lifted})
              AND c.relforcerowsecurity;
            FOREACH t IN ARRAY forced LOOP
                EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
                               current_schema(), t);
            END LOOP;
{adds}
            FOREACH t IN ARRAY forced LOOP
                EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                               current_schema(), t);
            END LOOP;
        END $$;"""  # noqa: S608 -- all identifiers are module-level constants
    )
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON external_calendar_events (patient_id)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    for table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {_fk_name(table)}")
