# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patient_message_thread_reads — what each clinician has read of a thread

A thread's read state was one ``clinician_last_read_at`` for the whole
practice, so one clinician opening a client's message marked it read for
every clinician with access. This table holds one marker per clinician per
thread; unread for a clinician is the client's messages newer than theirs.

EXISTING READ STATE IS CARRIED OVER. Each thread with a
``clinician_last_read_at`` gets a row, at that time, for every clinician
holding a live grant on its patient (``has_patient_access``) when this runs:
nothing anyone had read turns unread. The column is left in place and no
longer read; a later revision drops it.

The carry-over must see every thread and grant. Those tables are provisioned
``FORCE ROW LEVEL SECURITY`` and this runs as a role without BYPASSRLS and no
user GUC armed, so it is bracketed by the snapshot / suspend / restore that
``c9f3a6e2d817`` uses, restoring exactly the flags observed. The helpers are
written out here, as in every revision that does this, so the revision does
not change meaning when application code does.

``user_id`` carries the row, so the reconcile pass after the chain gives the
table the direct-ownership policy: each clinician reads and writes only their
own marker. Idempotent: it is fanned out once per practice schema.

Revision ID: b8e3f1a6d924
Revises: 8c5e65a0e56a
Create Date: 2026-11-24
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b8e3f1a6d924"
down_revision: str | Sequence[str] | None = "8c5e65a0e56a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _suspend_rls() -> None:
    """Record the carry-over tables' RLS flags, then stand them down."""
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            DROP TABLE IF EXISTS _thread_reads_rls_state;
            CREATE TEMP TABLE _thread_reads_rls_state AS
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname IN ('patient_message_threads', 'patient_clinicians',
                                'patient_message_thread_reads')
              AND c.relrowsecurity;

            FOR r IN SELECT * FROM _thread_reads_rls_state LOOP
                EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
                EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
            END LOOP;
        END $$;"""
    )


def _restore_rls() -> None:
    """Put back exactly the flags ``_suspend_rls`` observed."""
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            IF to_regclass('_thread_reads_rls_state') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN SELECT * FROM _thread_reads_rls_state LOOP
                IF r.rls_enabled THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
                IF r.rls_forced THEN
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
            END LOOP;
            DROP TABLE IF EXISTS _thread_reads_rls_state;
        END $$;"""
    )


def upgrade() -> None:
    op.execute(
        "CREATE TABLE IF NOT EXISTS patient_message_thread_reads ("
        "thread_id UUID NOT NULL REFERENCES patient_message_threads (id) ON DELETE CASCADE, "
        "user_id UUID NOT NULL, "
        "last_read_at TIMESTAMP WITH TIME ZONE NOT NULL, "
        "PRIMARY KEY (thread_id, user_id))"
    )
    _suspend_rls()
    try:
        op.execute(
            """
            INSERT INTO patient_message_thread_reads (thread_id, user_id, last_read_at)
            SELECT DISTINCT t.id, pc.user_id, t.clinician_last_read_at
            FROM patient_message_threads t
            JOIN patient_clinicians pc ON pc.patient_id = t.patient_id
            WHERE t.clinician_last_read_at IS NOT NULL
              AND has_patient_access(t.patient_id, pc.user_id::text)
            ON CONFLICT (thread_id, user_id) DO NOTHING
            """
        )
    finally:
        _restore_rls()


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS patient_message_thread_reads")
