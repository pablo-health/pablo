# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""widen remaining VARCHAR datetime columns in users + allowed_emails

Follow-up to ``b7de65c29385``. That revision absorbed the runtime
``_migrate_practice_columns`` patches but only covered the tables that
helper touched — patients, therapy_sessions, ehr_prompts, ehr_routes,
appointments, availability_rules, google_calendar_tokens,
ical_client_mappings, ical_sync_configs, clinician_profiles. The
``users`` and ``allowed_emails`` tables were originally created with
``VARCHAR(30)`` columns for their datetime values (see migration
``d20c4753ded3``) and were never widened because the runtime patch
didn't include them. Same latent bug class: anything that hydrates one
of these into a pydantic ``datetime`` field reads the Postgres text
form (``YYYY-MM-DD HH:MM:SS.ffffff+00``) and pydantic v2.13 rejects
the ``+00`` suffix.

Today the application reads tenant-local ``users`` only through paths
that don't go through a strict-datetime pydantic model, so the symptom
hasn't surfaced. Widening preemptively closes the gap before a future
caller trips on it, and keeps the tenant template uniform — every
datetime in a tenant schema is ``TIMESTAMPTZ``.

Same idempotency strategy as ``b7de65c29385``: introspect
``information_schema`` before each ALTER so the migration is a true
no-op on tenants that have already been hand-widened (none today —
this revision and ``b7de65c29385`` together are the full conversion).

Revision ID: a6af3834b782
Revises: b7de65c29385
Create Date: 2026-05-19 22:13:10.730421
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "a6af3834b782"
down_revision: str | Sequence[str] | None = "b7de65c29385"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TIMESTAMPTZ_COLUMNS: list[tuple[str, str]] = [
    ("allowed_emails", "added_at"),
    ("users", "created_at"),
    ("users", "baa_accepted_at"),
    ("users", "mfa_enrolled_at"),
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
    """Widen the remaining VARCHAR datetime columns to TIMESTAMPTZ."""
    schema = _current_schema()
    for table, column in _TIMESTAMPTZ_COLUMNS:
        if not _table_exists(schema, table):
            continue
        if _column_data_type(schema, table, column) != "character varying":
            continue
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE"
            f" USING CASE WHEN {column}::text = '' THEN NULL"
            f" ELSE {column}::text::timestamptz END"
        )


def downgrade() -> None:
    """Reverse to VARCHAR(30) — best-effort round-trip via ``::text``."""
    schema = _current_schema()
    for table, column in reversed(_TIMESTAMPTZ_COLUMNS):
        if not _table_exists(schema, table):
            continue
        if _column_data_type(schema, table, column) != "timestamp with time zone":
            continue
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE VARCHAR(30) USING {column}::text"
        )
