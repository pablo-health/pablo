# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The problem list: a client's diagnoses as rows, and the source of truth for them

``patient_problems`` holds one row per problem — a label, an optional ICD-10-CM
code, active / rule-out / resolved, an order. ``patients.diagnosis`` stops
being something anyone types and becomes the display line derived from the
active rows. ``diagnostic_assessments.problem_id`` lets a criteria worksheet
point at the problem it supports.

EXISTING DIAGNOSES ARE CARRIED OVER, NOT LOST. Each patient whose free-text
``diagnosis`` is not blank gets one active problem holding that text. When the
text names exactly one ICD-10-CM-shaped code, the code is lifted out into
``icd10_code`` and the rest is the label; with none, or several, the code
stays NULL and the whole text is the label, because choosing one of several
would be a guess. ``added_by`` is NULL on these rows: nobody added them, they
were already there. ``diagnosis`` is then rewritten as the derived line, which
for a row with a code reads ``<label> (<code>)``.

The carry-over has to see every patient, and ``patients`` is provisioned
``FORCE ROW LEVEL SECURITY`` while this runs as a role without BYPASSRLS and
with no user GUC armed — so a plain SELECT returns nothing and the carry-over
would silently do nothing. It is bracketed by the same snapshot / suspend /
restore that ``c1b7e4a92d53`` uses, restoring exactly the flags observed.

The parsing is written out here rather than imported from ``app.problems``: a
migration describes one moment, and must not change meaning when the
application's parser does. A patient that already has a problem row is
skipped, so a re-run carries nothing over twice.

The reconcile pass that follows every migration gives the new table the
``has_patient_access`` row policy, like ``patient_medications``.

Revision ID: c9f3a6e2d817
Revises: b4e8d1c7a925
Create Date: 2026-10-06
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c9f3a6e2d817"
down_revision: str | Sequence[str] | None = "b4e8d1c7a925"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ICD10_IN_TEXT = re.compile(r"\b[A-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?\b")
_LABEL_TRIM = " \t-:;,()[]" + chr(0x2013) + chr(0x2014)
_LABEL_MAX = 255


def _split(text: str) -> tuple[str, str | None]:
    cleaned = " ".join(text.split())
    codes = _ICD10_IN_TEXT.findall(cleaned)
    if len(codes) != 1:
        return cleaned, None
    label = " ".join(_ICD10_IN_TEXT.sub(" ", cleaned).split()).strip(_LABEL_TRIM)
    return (label or cleaned), codes[0]


def _suspend_rls() -> None:
    """Record ``patients``' RLS flags, then stand them down (see the docstring)."""
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            DROP TABLE IF EXISTS _problem_list_rls_state;
            CREATE TEMP TABLE _problem_list_rls_state AS
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname = 'patients'
              AND c.relrowsecurity;

            FOR r IN SELECT * FROM _problem_list_rls_state LOOP
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
            IF to_regclass('_problem_list_rls_state') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN SELECT * FROM _problem_list_rls_state LOOP
                IF r.rls_enabled THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
                IF r.rls_forced THEN
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
            END LOOP;
            DROP TABLE IF EXISTS _problem_list_rls_state;
        END $$;"""
    )


def _carry_over_diagnoses() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT p.id, p.diagnosis FROM patients p "
            "WHERE p.diagnosis IS NOT NULL AND btrim(p.diagnosis) <> '' "
            "AND NOT EXISTS (SELECT 1 FROM patient_problems pp WHERE pp.patient_id = p.id)"
        )
    ).all()
    now = datetime.now(UTC)
    for patient_id, diagnosis in rows:
        label, code = _split(diagnosis)
        label = label[:_LABEL_MAX]
        conn.execute(
            sa.text(
                "INSERT INTO patient_problems "
                "(id, patient_id, label, icd10_code, status, position, added_at, updated_at) "
                "VALUES (:id, :patient_id, :label, :code, 'active', 0, :now, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "patient_id": patient_id,
                "label": label,
                "code": code,
                "now": now,
            },
        )
        conn.execute(
            sa.text("UPDATE patients SET diagnosis = :line WHERE id = :patient_id"),
            {"line": f"{label} ({code})" if code else label, "patient_id": patient_id},
        )


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS patient_problems (
            id             UUID         PRIMARY KEY,
            patient_id     UUID         NOT NULL
                REFERENCES patients (id) ON DELETE CASCADE,
            label          VARCHAR(255) NOT NULL,
            icd10_code     VARCHAR(10),
            status         VARCHAR(16)  NOT NULL,
            onset_date     DATE,
            position       INTEGER      NOT NULL,
            source_note_id UUID
                REFERENCES notes (id) ON DELETE SET NULL,
            added_by       UUID,
            added_at       TIMESTAMPTZ  NOT NULL,
            resolved_at    TIMESTAMPTZ,
            updated_at     TIMESTAMPTZ  NOT NULL,
            deleted_at     TIMESTAMPTZ,
            CONSTRAINT ck_patient_problems_status
                CHECK (status IN ('active', 'rule_out', 'resolved'))
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_problems_patient_id "
        "ON patient_problems (patient_id);"
    )
    op.execute(
        "ALTER TABLE diagnostic_assessments ADD COLUMN IF NOT EXISTS problem_id UUID "
        "REFERENCES patient_problems (id) ON DELETE SET NULL;"
    )

    _suspend_rls()
    try:
        _carry_over_diagnoses()
    finally:
        _restore_rls()


def downgrade() -> None:
    op.execute("ALTER TABLE diagnostic_assessments DROP COLUMN IF EXISTS problem_id;")
    op.execute("DROP TABLE IF EXISTS patient_problems CASCADE;")
