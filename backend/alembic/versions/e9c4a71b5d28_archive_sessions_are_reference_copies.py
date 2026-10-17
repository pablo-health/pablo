# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""therapy_sessions: an archive-imported visit is a reference copy too

``d3b81f6c5a27`` gave a note read from a single document its own session
status, ``imported``: a visit documented and signed in the system where it
happened, never offered for signing here. The archive importer kept landing
its visits as ``finalized``, so each opened with a ``Sign note`` action, an
``AI Generated`` badge and nothing saying where it came from.

Archive sessions move to ``imported``. They are told apart by their note:
the archive importer writes the note's provenance into its content under
``__source``, which no other writer of a session note does. The signer the
export named is copied onto the session's ``source_signer``, as the
single-document import records it.

THE UPDATE MUST SEE EVERY ROW. Per-tenant tables are provisioned ``FORCE ROW
LEVEL SECURITY`` and the migration runs as a role without BYPASSRLS, with no
session GUC set, so under the policies it would see nothing to move. Both
tables are read with RLS stood down, using the snapshot/suspend/restore pair
``d3b81f6c5a27`` uses; the flags are put back exactly as they were observed.

Idempotent, like every revision in this chain.

Revision ID: e9c4a71b5d28
Revises: d3b81f6c5a27
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e9c4a71b5d28"
down_revision: str | Sequence[str] | None = "d3b81f6c5a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _suspend_rls() -> None:
    """Record the two tables' RLS flags, then stand them down."""
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            DROP TABLE IF EXISTS _archive_sessions_rls_state;
            CREATE TEMP TABLE _archive_sessions_rls_state AS
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname IN ('therapy_sessions', 'notes')
              AND c.relrowsecurity;

            FOR r IN SELECT * FROM _archive_sessions_rls_state LOOP
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
            IF to_regclass('_archive_sessions_rls_state') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN SELECT * FROM _archive_sessions_rls_state LOOP
                IF r.rls_enabled THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
                IF r.rls_forced THEN
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
            END LOOP;
            DROP TABLE IF EXISTS _archive_sessions_rls_state;
        END $$;"""
    )


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    _suspend_rls()
    try:
        op.execute(
            """
            UPDATE therapy_sessions s
                SET status = 'imported',
                    source_signer = COALESCE(
                        s.source_signer,
                        NULLIF(n.content #>> '{__source,signed_by}', '')
                    )
                FROM notes n
                WHERE n.session_id = s.id
                  AND s.source = 'imported'
                  AND s.status = 'finalized'
                  AND n.content ? '__source'
            """
        )
    finally:
        _restore_rls()


def downgrade() -> None:
    _suspend_rls()
    try:
        op.execute(
            """
            UPDATE therapy_sessions s
                SET status = 'finalized'
                FROM notes n
                WHERE n.session_id = s.id
                  AND s.status = 'imported'
                  AND n.content ? '__source'
            """
        )
    finally:
        _restore_rls()
