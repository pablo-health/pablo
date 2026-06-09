# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs append-only trigger (PABLO-5zy)

Makes every per-tenant ``audit_logs`` table append-only. The app connects
as the table OWNER under ``FORCE ROW LEVEL SECURITY``, and the retention
cron shares that same DB role, so a plain ``REVOKE UPDATE, DELETE`` is
useless (owners bypass table privileges) and would also break the
legitimate retention purge. A ``BEFORE`` trigger fires even for the owner,
so it is the correct mechanism.

The trigger raises on any UPDATE or DELETE unless the session has armed the
transaction-scoped GUC ``app.allow_audit_purge = 'on'`` — which only the
retention cron (``app.jobs.audit_retention_cron._delete_expired``) does,
inside the same transaction as its DELETE. ``current_setting(..., true)``
returns NULL when the GUC is unset, and ``NULL = 'on'`` is falsy, so an
unset GUC correctly blocks the operation (fail-closed).

This migration backfills the function + trigger into every existing tenant
schema via the per-tenant fan-out (``app.db.migrate_tenants``), which sets
``search_path`` to each tenant schema before invoking alembic. New tenants
get the same DDL from ``tenant_template.sql`` (regenerated from this
migration's HEAD).

Idempotent by construction: ``CREATE OR REPLACE FUNCTION`` plus
``DROP TRIGGER IF EXISTS`` before ``CREATE TRIGGER``, so the fan-out can
replay it safely against schemas that may have partially completed prior
runs.

Threat model — this is DEFENSE-IN-DEPTH, not an absolute boundary. Because
the app shares the table-owner role with the retention cron, code running
arbitrary SQL as that role could still bypass the trigger: it could
``SET LOCAL app.allow_audit_purge = 'on'`` itself, or (as owner)
``ALTER TABLE audit_logs DISABLE TRIGGER`` then mutate. The trigger
reliably stops accidental and ORM-level UPDATE/DELETE and forces deliberate
intent, which is the realistic insider/compromise model here. Absolute
immutability against a fully-compromised app process requires role
separation (app role REVOKE'd + NOBYPASSRLS, retention run as a distinct
privileged role/job) — tracked as a follow-up, not done here.

Revision ID: b33a493310b6
Revises: d1a47f3c9b62
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b33a493310b6"
down_revision: str | Sequence[str] | None = "d1a47f3c9b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {schema}.audit_logs_append_only()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF current_setting('app.allow_audit_purge', true) = 'on' THEN
                RETURN COALESCE(OLD, NEW);
            END IF;
            RAISE EXCEPTION USING
                MESSAGE = 'audit_logs is append-only (' || TG_OP || ' blocked)',
                ERRCODE = 'check_violation';
        END;
        $$
        """
    )
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_logs_append_only ON {schema}.audit_logs"
    )
    op.execute(
        f"""
        CREATE TRIGGER audit_logs_append_only
            BEFORE UPDATE OR DELETE ON {schema}.audit_logs
            FOR EACH ROW
            EXECUTE FUNCTION {schema}.audit_logs_append_only()
        """
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(
        f"DROP TRIGGER IF EXISTS audit_logs_append_only ON {schema}.audit_logs"
    )
    op.execute(f"DROP FUNCTION IF EXISTS {schema}.audit_logs_append_only()")
