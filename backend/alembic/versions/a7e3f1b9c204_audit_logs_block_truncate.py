# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs append-only: block TRUNCATE for the app role

The b33a493310b6 ``audit_logs_append_only`` trigger keeps audit rows
append-only against UPDATE/DELETE, but a row-level trigger does NOT fire on
TRUNCATE (TRUNCATE is a statement-level, table-wide operation). The app role
owns the table, so without this it could wipe the entire trail in one
``TRUNCATE`` — defeating append-only just as thoroughly as a mass DELETE.

This adds a statement-level ``BEFORE TRUNCATE`` trigger that raises unless the
same transaction-scoped ``app.allow_audit_purge = 'on'`` GUC the retention
path arms is set — mirroring the b33a493310b6 append-only trigger exactly, so
an authorized purge (or a deliberate operator/test reset) stays possible while
the application role cannot wipe the trail. Nothing wipes ``audit_logs``
without arming that GUC: retention DELETEs (GUC-armed), tenant teardown uses
``DROP SCHEMA`` (not TRUNCATE), and fixture resets arm the GUC explicitly.

This is the OSS (single-DB-role, trigger-based) counterpart to the managed
build's privilege-based control (``REVOKE TRUNCATE`` from the app role).

Backfills via the per-tenant fan-out; new tenants get it from
``tenant_template.sql`` (regenerated from this migration's HEAD). Idempotent:
``CREATE OR REPLACE FUNCTION`` + ``DROP TRIGGER IF EXISTS`` before create.

Revision ID: a7e3f1b9c204
Revises: b2e8c4f6a1d3
Create Date: 2026-06-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a7e3f1b9c204"
down_revision: str | Sequence[str] | None = "b2e8c4f6a1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.audit_logs_no_truncate()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF current_setting('app.allow_audit_purge', true) = 'on' THEN
                RETURN NULL;
            END IF;
            RAISE EXCEPTION USING
                MESSAGE = 'audit_logs is append-only (TRUNCATE blocked)',
                ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    op.execute(f"DROP TRIGGER IF EXISTS audit_logs_no_truncate ON {schema}.audit_logs")
    op.execute(
        f"""
        CREATE TRIGGER audit_logs_no_truncate
            BEFORE TRUNCATE ON {schema}.audit_logs
            FOR EACH STATEMENT
            EXECUTE FUNCTION {schema}.audit_logs_no_truncate()
        """
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(f"DROP TRIGGER IF EXISTS audit_logs_no_truncate ON {schema}.audit_logs")
    op.execute(f"DROP FUNCTION IF EXISTS {schema}.audit_logs_no_truncate()")
