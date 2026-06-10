# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Add the missing same-schema foreign keys to the clinical core tables.

The original clinical tables — ``patients``, ``therapy_sessions``,
``notes``, ``appointments``, ``outcome_measures``,
``diagnostic_assessments``, ``chat_conversations``,
``patient_medications`` — carry ``patient_id`` / ``session_id`` /
``appointment_id`` columns with **no referential integrity**. They came
over from a document store that didn't model relations, and the FKs never
followed. The modules written Postgres-first (prescribing, supervision,
compliance documents, chat messages) all FK properly to ``patients(id)``
/ ``prescribing_encounters(id)`` / etc. — so today ``patient_documents.
patient_id`` is enforced while ``notes.patient_id`` right beside it is
not. Close that gap: every same-schema reference gets a real FK.

Policy:
  * ``patient_id`` (NOT NULL, the row belongs to a patient) ->
    ``patients(id) ON DELETE CASCADE`` — matches the existing patient FKs
    (``patient_documents``, ``patient_clinicians``, ``prescribing_*``) and
    makes a patient hard-purge clean up the whole chart.
  * ``session_id`` / ``appointment_id`` / ``recurring_appointment_id``
    (nullable secondary links) -> ``ON DELETE SET NULL`` — dropping a
    session must not delete the note/measure that referenced it.

Deliberately NOT FK'd:
  * ``*.user_id`` and the actor columns (``created_by`` etc.) — they point
    at ``platform.users``, a different schema; a cross-schema FK isn't
    available in the schema-per-tenant model. Go-forward integrity for
    those rides on the app + RLS, same as before.
  * ``audit_logs.patient_id`` / ``audit_logs.session_id`` — a forensic log
    must survive a hard-purge of the thing it references, so it keeps the
    "identifier as recorded" stance (see the audit columns in
    ``c1d7e4a9f2b6``).

Same-schema, so the constraints are added to ``current_schema()`` (the
tenant or the ``practice`` template — never ``platform``; see
``alembic/env.py``). Idempotent under the per-tenant fan-out: each ADD is
guarded on the constraint not already existing. Existing rows are
validated — a pre-launch tenant with an orphan ``patient_id`` will fail
loudly here, which is the right time to find it.

RLS interaction: ``ADD CONSTRAINT ... FOREIGN KEY`` validates existing
rows with a scan of both tables. When the migration runs as a role
without BYPASSRLS against FORCE-RLS tables, that scan is policy-filtered
— the session GUC the policies key on is unset, every referenced row is
invisible, and perfectly valid data fails with a phantom FK violation.
So each involved table's RLS state is snapshotted, RLS is suspended for
the adds, and the state is restored exactly afterwards (mirrors the FK
restore in ``c1d7e4a9f2b6``). Validation stays honest — a real orphan
still aborts loudly.

Revision ID: e7c4b9a25f18
Revises: a3f8d21c5e90
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "e7c4b9a25f18"
down_revision: str | Sequence[str] | None = "a3f8d21c5e90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, referenced_table, on_delete). Constraint names follow the
# pg_dump default ``<table>_<column>_fkey`` so the regenerated template and
# the live DB agree.
_FOREIGN_KEYS: list[tuple[str, str, str, str]] = [
    # patient_id -> patients(id), CASCADE (row belongs to the patient).
    ("therapy_sessions", "patient_id", "patients", "CASCADE"),
    ("notes", "patient_id", "patients", "CASCADE"),
    ("appointments", "patient_id", "patients", "CASCADE"),
    ("outcome_measures", "patient_id", "patients", "CASCADE"),
    ("diagnostic_assessments", "patient_id", "patients", "CASCADE"),
    ("chat_conversations", "patient_id", "patients", "CASCADE"),
    ("patient_medications", "patient_id", "patients", "CASCADE"),
    ("ical_client_mappings", "patient_id", "patients", "CASCADE"),
    # session_id -> therapy_sessions(id), SET NULL (nullable secondary link).
    ("notes", "session_id", "therapy_sessions", "SET NULL"),
    ("outcome_measures", "session_id", "therapy_sessions", "SET NULL"),
    ("diagnostic_assessments", "session_id", "therapy_sessions", "SET NULL"),
    ("appointments", "session_id", "therapy_sessions", "SET NULL"),
    # appointment_id -> appointments(id), SET NULL (nullable secondary link).
    ("outcome_measures", "appointment_id", "appointments", "SET NULL"),
    ("diagnostic_assessments", "appointment_id", "appointments", "SET NULL"),
    # recurring_appointment_id -> appointments(id), SET NULL (self-reference).
    ("appointments", "recurring_appointment_id", "appointments", "SET NULL"),
]


def _fk_name(table: str, column: str) -> str:
    return f"{table}_{column}_fkey"


def _involved_tables() -> list[str]:
    seen: dict[str, None] = {}
    for table, _, ref_table, _ in _FOREIGN_KEYS:
        seen.setdefault(table, None)
        seen.setdefault(ref_table, None)
    return list(seen)


def _suspend_rls() -> None:
    table_list = ", ".join(f"'{t}'" for t in _involved_tables())
    op.execute(
        f"""DO $$
        DECLARE r RECORD;
        BEGIN
            DROP TABLE IF EXISTS _intra_fk_rls_state;
            CREATE TEMP TABLE _intra_fk_rls_state AS
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname IN ({table_list})
              AND c.relrowsecurity;

            FOR r IN SELECT * FROM _intra_fk_rls_state LOOP
                EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
                EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
            END LOOP;
        END $$;"""  # noqa: S608 -- table names are module-level constants
    )


def _restore_rls() -> None:
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            IF to_regclass('_intra_fk_rls_state') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN SELECT * FROM _intra_fk_rls_state LOOP
                IF r.rls_enabled THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
                IF r.rls_forced THEN
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
            END LOOP;
            DROP TABLE IF EXISTS _intra_fk_rls_state;
        END $$;"""
    )


def upgrade() -> None:
    _suspend_rls()
    for table, column, ref_table, on_delete in _FOREIGN_KEYS:
        name = _fk_name(table, column)
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
                        'FOREIGN KEY ({column}) '
                        'REFERENCES %I.{ref_table}(id) ON DELETE {on_delete}',
                        current_schema(), current_schema()
                    );
                END IF;
            END $$;"""  # noqa: S608 -- all identifiers are module-level constants
        )
    _restore_rls()


def downgrade() -> None:
    for table, column, _ref_table, _on_delete in reversed(_FOREIGN_KEYS):
        name = _fk_name(table, column)
        op.execute(
            f"""DO $$
            BEGIN
                EXECUTE format(
                    'ALTER TABLE %I.{table} DROP CONSTRAINT IF EXISTS {name}',
                    current_schema()
                );
            END $$;"""
        )
