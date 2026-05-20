# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""absorb runtime tenant column migrations from provisioning._migrate_practice_columns

The retiring ``app.db.provisioning._migrate_practice_columns`` was the
sole place these per-tenant column changes lived: it ran on every app
boot against every ``practice_*`` schema, with bare-except savepoints
swallowing failures. Tenants provisioned via the template path
(``_apply_tenant_template`` after the 2026-05-17 fix) never had this
run at provision time, so they inherited the template's stale shape
(VARCHAR datetime columns) and stayed broken until the next backend
revision booted and re-scanned. That's the chain that caused the
``GET /api/patients`` 500s in prod on 2026-05-19→20.

Absorbing the same statements into the alembic chain — and regenerating
``tenant_template.sql`` from the resulting head — means new tenants are
born correct and the runtime migrator can be deleted.

Idempotent on every tenant in fleet today:

* The two existing prod tenants were already migrated by a boot-time
  pass; for those, every column here is already ``timestamptz`` and
  every ``ADD COLUMN`` already exists.
* Freshly-template-provisioned tenants (post-cutover) start at this
  revision, so the chain runs from scratch.

Idempotency is enforced by introspecting ``information_schema`` before
each statement — no destructive ALTER fires on a column that already
matches the target shape. Avoids the table-rewrite cost of running
``ALTER COLUMN ... TYPE timestamptz`` against an already-timestamptz
column (which is functionally a no-op but locks + rewrites the table).

Revision ID: b7de65c29385
Revises: e7a2c91d5f8b
Create Date: 2026-05-19 21:24:39.327446
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "b7de65c29385"
down_revision: str | Sequence[str] | None = "e7a2c91d5f8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, "<sql column definition fragment>")
_ADD_COLUMNS: list[tuple[str, str, str]] = [
    ("ical_sync_configs", "consecutive_error_count", "INTEGER DEFAULT 0"),
    ("google_calendar_tokens", "consecutive_error_count", "INTEGER DEFAULT 0"),
    ("google_calendar_tokens", "last_sync_error", "TEXT"),
    ("therapy_sessions", "transcription_job_metadata", "JSONB"),
]


# (table, column) — all of these were created as VARCHAR by an earlier
# migration generation and the runtime patch widened them in place.
_TIMESTAMPTZ_COLUMNS: list[tuple[str, str]] = [
    ("patients", "last_session_date"),
    ("patients", "next_session_date"),
    ("patients", "created_at"),
    ("patients", "updated_at"),
    ("therapy_sessions", "session_date"),
    ("therapy_sessions", "created_at"),
    ("therapy_sessions", "scheduled_at"),
    ("therapy_sessions", "started_at"),
    ("therapy_sessions", "ended_at"),
    ("therapy_sessions", "updated_at"),
    ("therapy_sessions", "processing_started_at"),
    ("therapy_sessions", "processing_completed_at"),
    ("ehr_prompts", "updated_at"),
    ("ehr_routes", "last_success"),
    ("ehr_routes", "created_at"),
    ("ehr_routes", "updated_at"),
    ("appointments", "start_at"),
    ("appointments", "end_at"),
    ("appointments", "created_at"),
    ("appointments", "updated_at"),
    ("availability_rules", "created_at"),
    ("availability_rules", "updated_at"),
    ("google_calendar_tokens", "last_synced_at"),
    ("google_calendar_tokens", "connected_at"),
    ("ical_client_mappings", "created_at"),
    ("ical_sync_configs", "last_synced_at"),
    ("ical_sync_configs", "connected_at"),
    ("clinician_profiles", "joined_at"),
]


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def _column_data_type(schema: str, table: str, column: str) -> str | None:
    return (
        op.get_bind()
        .execute(
            text(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_schema = :schema"
                " AND table_name = :table"
                " AND column_name = :column"
            ),
            {"schema": schema, "table": table, "column": column},
        )
        .scalar_one_or_none()
    )


def _table_exists(schema: str, table: str) -> bool:
    return (
        op.get_bind()
        .execute(
            text(
                "SELECT 1 FROM information_schema.tables"
                " WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": schema, "table": table},
        )
        .scalar_one_or_none()
        is not None
    )


def upgrade() -> None:
    """Apply the column additions + VARCHAR->TIMESTAMPTZ widenings."""
    schema = _current_schema()

    for table, column, definition in _ADD_COLUMNS:
        if not _table_exists(schema, table):
            continue
        # ADD COLUMN IF NOT EXISTS is server-side idempotent (PG 9.6+);
        # the table-exists guard above is for the (degenerate) case of
        # a tenant schema that's missing the table entirely.
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")

    for table, column in _TIMESTAMPTZ_COLUMNS:
        if not _table_exists(schema, table):
            continue
        current_type = _column_data_type(schema, table, column)
        if current_type != "character varying":
            # Already widened (or column missing entirely on a partially
            # provisioned schema). Skip; ALTER COLUMN ... TYPE with the
            # round-trip USING clause would rewrite the table for nothing.
            continue
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE"
            f" USING CASE WHEN {column}::text = '' THEN NULL"
            f" ELSE {column}::text::timestamptz END"
        )


def downgrade() -> None:
    """Reverse the column additions + widenings.

    Best-effort: VARCHAR(30) is the original shape the column came from
    (matches the storage format the legacy ``utc_now().isoformat()`` path
    emitted). Data with subsecond precision survives the round-trip via
    the format ``YYYY-MM-DD HH:MM:SS.ffffff+00`` produced by
    ``timestamptz::text``. The ``ADD COLUMN`` reversals are unconditional
    DROPs — anything depending on those columns would block this.
    """
    schema = _current_schema()

    # Reverse the type widenings in reverse order — purely cosmetic
    # (each column is independent) but mirrors the upgrade orientation.
    for table, column in reversed(_TIMESTAMPTZ_COLUMNS):
        if not _table_exists(schema, table):
            continue
        current_type = _column_data_type(schema, table, column)
        if current_type != "timestamp with time zone":
            continue
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR(30) USING {column}::text"
        )

    for table, column, _definition in reversed(_ADD_COLUMNS):
        if not _table_exists(schema, table):
            continue
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS {column}")
