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

Per table, in this one revision:

1. ``ADD CONSTRAINT ... NOT VALID`` - enforced for new rows from here on,
   existing rows not yet checked.
2. Delete the orphans: rows whose ``patient_id`` names no ``patients`` row.
   They exist, left by the purge before ``PABLO-8gna.37``, which deleted the
   client and not these. An orphan has no client to belong to and nothing can
   reach it; it is the residue the purge promised to remove.
3. ``VALIDATE CONSTRAINT``.

Deleting an orphan intake assignment takes its answers, signatures, artifacts,
review events and chart proposals with it, by their existing cascades. No
``RESTRICT`` sits on any of those chains.

ROW-LEVEL SECURITY. The migration runs as a role without BYPASSRLS, and
``env.py`` arms no principal, so every policy-filtered read sees no rows. For
``patients`` that is the dangerous direction: an orphan check against an
invisible ``patients`` table finds every row orphaned and would delete all of
them. So RLS is snapshotted and stood down on ``patients`` and the seven
tables for the work, and put back exactly as observed afterwards (the
``e7c4b9a25f18`` / ``c1b7e4a92d53`` pattern). ``VALIDATE`` is not
policy-filtered either way.

``external_calendar_events`` gets an index on ``patient_id``: it had none, and
without one every client deleted would scan the table for rows to cascade to.

Idempotent under the per-tenant fan-out: each add is guarded on the constraint
not existing, each index on ``IF NOT EXISTS``, and validating a valid
constraint is a no-op. The downgrade drops the keys and the index; the orphans
it deleted are not restored.

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


def _suspend_rls() -> None:
    """Record RLS flags on ``patients`` and the seven tables, then stand them down.

    Only tables with RLS enabled are captured, so ``_restore_rls`` leaves a
    table that never had it exactly as it found it.
    """
    table_list = ", ".join(f"'{t}'" for t in ("patients", *_TABLES))
    op.execute(
        f"""DO $$
        DECLARE r RECORD;
        BEGIN
            DROP TABLE IF EXISTS _patient_fk_cascade_rls_state;
            CREATE TEMP TABLE _patient_fk_cascade_rls_state AS
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname IN ({table_list})
              AND c.relrowsecurity;

            FOR r IN SELECT * FROM _patient_fk_cascade_rls_state LOOP
                EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
                EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
            END LOOP;
        END $$;"""  # noqa: S608 -- table names are module-level constants
    )


def _restore_rls() -> None:
    """Put back exactly the flags ``_suspend_rls`` observed."""
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            IF to_regclass('_patient_fk_cascade_rls_state') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN SELECT * FROM _patient_fk_cascade_rls_state LOOP
                IF r.rls_enabled THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
                IF r.rls_forced THEN
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
            END LOOP;
            DROP TABLE IF EXISTS _patient_fk_cascade_rls_state;
        END $$;"""
    )


def _add_not_valid(table: str) -> None:
    name = _fk_name(table)
    op.execute(
        f"""DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint con
                JOIN pg_class c ON con.conrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE n.nspname = current_schema()
                  AND c.relname = '{table}'
                  AND con.conname = '{name}'
            ) THEN
                EXECUTE format(
                    'ALTER TABLE %I.{table} ADD CONSTRAINT {name} '
                    'FOREIGN KEY (patient_id) '
                    'REFERENCES %I.patients(id) ON DELETE CASCADE NOT VALID',
                    current_schema(), current_schema()
                );
            END IF;
        END $$;"""  # noqa: S608 -- all identifiers are module-level constants
    )


def upgrade() -> None:
    _suspend_rls()
    try:
        for table in _TABLES:
            _add_not_valid(table)
        for table in _TABLES:
            # RLS is down on both sides, so "no patients row" means none
            # exists, not merely that this role cannot see it.
            op.execute(
                f"DELETE FROM {table} t "  # noqa: S608 -- module-level constant
                "WHERE t.patient_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM patients p WHERE p.id = t.patient_id)"
            )
        for table in _TABLES:
            op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {_fk_name(table)}")
    finally:
        # A failure aborts the transaction and rolls the flags back with
        # everything else; restoring in ``finally`` covers a driver that has
        # already committed the DDL.
        _restore_rls()
    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON external_calendar_events (patient_id)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    for table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {_fk_name(table)}")
