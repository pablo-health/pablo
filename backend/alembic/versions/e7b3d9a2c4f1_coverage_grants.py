# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Coverage: a colleague accepts to cover an away period, with time-boxed access

``clinician_away_periods`` gains ``coverage_status`` (``requested``,
``accepted`` or ``declined``; ``NULL`` exactly when nobody is named) and
``coverage_responded_at``. A period that already names a colleague starts
as ``requested``: naming somebody never granted anything, and still does not.

``patient_clinicians`` gains ``starts_at``, when a grant takes effect, and
``away_period_id``, the away period a ``covering`` grant covers.
``has_patient_access`` now requires ``starts_at`` to have passed as well as
``expires_at`` to be ahead, so coverage booked in advance grants nothing
before the away dates begin. Its body keeps the SQL-standard ``RETURN`` form
of ``a7e3c5d19b42``, bound to its own schema's table.

``audit_logs`` gains a SELECT-only policy, ``rls_audit_coverage_read``: a
clinician reads the rows their covering colleague wrote on their clients
under the coverage of one of their own away periods. Policies are OR'd, so it
widens reads and nothing else. ``enable_rls_on_schema`` creates the identical
policy; ``test_audit_policy_parity`` holds the two together.

Idempotent, like every revision in this chain: it is fanned out once per
practice schema.

Revision ID: e7b3d9a2c4f1
Revises: d2a7e4c91b58
Create Date: 2026-10-24
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e7b3d9a2c4f1"
down_revision: str | Sequence[str] | None = "d2a7e4c91b58"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

POLICY_NAME = "rls_audit_coverage_read"


def coverage_read_using(schema: str) -> str:
    """The policy's USING clause for the audit table in ``schema``.

    ``schema`` is ``current_schema()`` of the migration's own connection,
    never input.
    """
    return (
        "patient_id IS NOT NULL "  # noqa: S608
        "AND (changes ->> 'coverage_period_id') IS NOT NULL "
        f"AND EXISTS (SELECT 1 FROM {schema}.clinician_away_periods ap "
        "WHERE ap.id::text = (audit_logs.changes ->> 'coverage_period_id') "
        "AND ap.user_id::text = current_setting('app.current_user_id', true) "
        "AND ap.covering_user_id::text = audit_logs.user_id) "
        f"AND {schema}.has_patient_access("
        "patient_id, current_setting('app.current_user_id', true))"
    )


def _has_patient_access(schema: str, *, honour_starts_at: bool) -> str:
    starts = "AND (pc.starts_at IS NULL OR pc.starts_at <= now()) " if honour_starts_at else ""
    return (
        f"CREATE OR REPLACE FUNCTION {schema}.has_patient_access("  # noqa: S608
        "p_patient_id uuid, p_user_id character varying) "
        "RETURNS boolean LANGUAGE sql STABLE "
        f"RETURN EXISTS (SELECT 1 FROM {schema}.patient_clinicians pc "
        "WHERE pc.patient_id = p_patient_id "
        "AND pc.user_id::text = p_user_id "
        f"{starts}"
        "AND (pc.expires_at IS NULL OR pc.expires_at > now()))"
    )


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()
    # search_path is set to the tenant schema by the runner; unqualified table
    # refs resolve to that schema.
    op.execute(
        "ALTER TABLE clinician_away_periods "
        "ADD COLUMN IF NOT EXISTS coverage_status VARCHAR(10), "
        "ADD COLUMN IF NOT EXISTS coverage_responded_at TIMESTAMPTZ;"
    )
    op.execute(
        "UPDATE clinician_away_periods SET coverage_status = 'requested' "
        "WHERE covering_user_id IS NOT NULL AND coverage_status IS NULL;"
    )
    op.execute(
        "ALTER TABLE clinician_away_periods "
        "DROP CONSTRAINT IF EXISTS ck_clinician_away_periods_coverage_status, "
        "DROP CONSTRAINT IF EXISTS ck_clinician_away_periods_coverage_named;"
    )
    op.execute(
        "ALTER TABLE clinician_away_periods "
        "ADD CONSTRAINT ck_clinician_away_periods_coverage_status "
        "CHECK (coverage_status IN ('requested', 'accepted', 'declined')), "
        "ADD CONSTRAINT ck_clinician_away_periods_coverage_named "
        "CHECK ((covering_user_id IS NULL) = (coverage_status IS NULL));"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_clinician_away_periods_covering_user "
        "ON clinician_away_periods (covering_user_id);"
    )
    op.execute(
        "ALTER TABLE patient_clinicians "
        "ADD COLUMN IF NOT EXISTS starts_at TIMESTAMPTZ, "
        "ADD COLUMN IF NOT EXISTS away_period_id UUID "
        "REFERENCES clinician_away_periods (id) ON DELETE SET NULL;"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_patient_clinicians_away_period_id "
        "ON patient_clinicians (away_period_id);"
    )
    op.execute(_has_patient_access(schema, honour_starts_at=True))
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {schema}.audit_logs")
    op.execute(
        f"CREATE POLICY {POLICY_NAME} ON {schema}.audit_logs "
        f"FOR SELECT USING ({coverage_read_using(schema)})"
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {schema}.audit_logs")
    op.execute(_has_patient_access(schema, honour_starts_at=False))
    op.execute("DROP INDEX IF EXISTS ix_patient_clinicians_away_period_id;")
    op.execute(
        "ALTER TABLE patient_clinicians "
        "DROP COLUMN IF EXISTS away_period_id, DROP COLUMN IF EXISTS starts_at;"
    )
    op.execute("DROP INDEX IF EXISTS ix_clinician_away_periods_covering_user;")
    op.execute(
        "ALTER TABLE clinician_away_periods "
        "DROP CONSTRAINT IF EXISTS ck_clinician_away_periods_coverage_named, "
        "DROP CONSTRAINT IF EXISTS ck_clinician_away_periods_coverage_status, "
        "DROP COLUMN IF EXISTS coverage_responded_at, "
        "DROP COLUMN IF EXISTS coverage_status;"
    )
