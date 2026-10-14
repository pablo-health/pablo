# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""therapy_sessions: an imported note is a reference copy, not a note to sign

An imported note records an earlier visit that was documented and signed in
the system where it happened. It used to land in ``pending_review`` like a
drafted note, so it was offered for signing, and signing it stamped this
clinician's name and today's date on it. It now has its own status,
``imported``, which no to-sign surface reads.

Imported sessions still awaiting review move to ``imported``. One whose note
was already signed here is left exactly as it is, whatever its status.

``source_signer`` keeps who signed the note in its own system, as the
document writes it. It is a string, never a user reference.
``source_document_id`` links the session to the uploaded file itself, kept
in the client's documents; existing imports kept only the text, so it starts
empty for them.

THE UPDATE MUST SEE EVERY ROW, AND SO MUST ITS CHECK. Per-tenant tables are
provisioned ``FORCE ROW LEVEL SECURITY`` and the migration runs as a role
without BYPASSRLS, with no session GUC set, so under the policies it would see
no session to move — and, worse, no signed note, which would read as "nothing
here was signed". Both tables are read with RLS stood down, using the
snapshot/suspend/restore pair ``c1b7e4a92d53`` uses for the same reason; the
flags are put back exactly as they were observed.

Idempotent, like every revision in this chain.

Revision ID: d3b81f6c5a27
Revises: a7e3c5d19b42
Create Date: 2026-10-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3b81f6c5a27"
down_revision: str | Sequence[str] | None = "a7e3c5d19b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _suspend_rls() -> None:
    """Record the two tables' RLS flags, then stand them down.

    Only tables with RLS enabled are captured, so ``_restore_rls`` leaves a
    table that never had it exactly as it was. The temp table is named for this
    revision so it cannot collide with another migration's.
    """
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            DROP TABLE IF EXISTS _imported_sessions_rls_state;
            CREATE TEMP TABLE _imported_sessions_rls_state AS
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname IN ('therapy_sessions', 'notes')
              AND c.relrowsecurity;

            FOR r IN SELECT * FROM _imported_sessions_rls_state LOOP
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
            IF to_regclass('_imported_sessions_rls_state') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN SELECT * FROM _imported_sessions_rls_state LOOP
                IF r.rls_enabled THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
                IF r.rls_forced THEN
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
            END LOOP;
            DROP TABLE IF EXISTS _imported_sessions_rls_state;
        END $$;"""
    )


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        ALTER TABLE therapy_sessions ADD COLUMN IF NOT EXISTS source_signer text;
        ALTER TABLE therapy_sessions ADD COLUMN IF NOT EXISTS source_document_id uuid;
        ALTER TABLE therapy_sessions
            DROP CONSTRAINT IF EXISTS therapy_sessions_source_document_id_fkey;
        ALTER TABLE therapy_sessions
            ADD CONSTRAINT therapy_sessions_source_document_id_fkey
            FOREIGN KEY (source_document_id) REFERENCES patient_documents (id)
            ON DELETE SET NULL;
        """
    )
    _suspend_rls()
    try:
        op.execute(
            """
            UPDATE therapy_sessions s
                SET status = 'imported'
                WHERE s.source = 'imported'
                  AND s.status = 'pending_review'
                  AND NOT EXISTS (
                      SELECT 1 FROM notes n
                      WHERE n.session_id = s.id AND n.finalized_at IS NOT NULL
                  )
            """
        )
    finally:
        _restore_rls()


def downgrade() -> None:
    _suspend_rls()
    try:
        op.execute(
            "UPDATE therapy_sessions SET status = 'pending_review' WHERE status = 'imported'"
        )
    finally:
        _restore_rls()
    op.execute(
        """
        ALTER TABLE therapy_sessions DROP COLUMN IF EXISTS source_document_id;
        ALTER TABLE therapy_sessions DROP COLUMN IF EXISTS source_signer;
        """
    )
