# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""soft-delete columns for patients/sessions/notes

Adds ``deleted_at TIMESTAMPTZ NULL`` to ``patients``, ``therapy_sessions``,
and ``notes``. This is the OSS-side foundation for the BAA 2026-05-05
three-stage deletion model (THERAPY-nyb):

  Stage 1 (now): user-facing delete sets ``deleted_at``; rows become
                 invisible to read paths but stay on disk.
  Stage 2 (T+30, THERAPY-cgy): a purge cron physically deletes rows
                 whose ``deleted_at < now() - interval '30 days'``.
  Stage 3 (T+30+, separate scope): tombstone rows in compliance schema.

Notes on scope vs. brief:
  - Transcripts are NOT a separate table in this schema — they live as
    ``transcript JSONB NOT NULL`` on ``therapy_sessions``. The
    therapy-session soft-delete therefore covers transcript visibility
    transitively. No extra table to alter here.
  - The migration is idempotent (``IF NOT EXISTS`` / ``IF EXISTS``).
    It runs once per tenant via the per-tenant fan-out in
    ``backend/alembic/env.py`` (``config.attributes['target_schema']``);
    DDL operates on the search-path-scoped tenant schema, so no manual
    iteration over ``platform.practices`` is needed inside ``upgrade()``.
  - A partial index on each table — ``WHERE deleted_at IS NOT NULL`` —
    keeps the live-row query path index-free for this column (the WHERE
    excludes most rows) but gives the day-30 purge cron a tight scan.

Revision ID: d4f8a1c92e35
Revises: c8a9d3e4f206
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d4f8a1c92e35"
down_revision: str | Sequence[str] | None = "c8a9d3e4f206"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table_name, partial_index_name)
_SOFT_DELETE_TARGETS: tuple[tuple[str, str], ...] = (
    ("patients", "ix_patients_deleted_at_partial"),
    ("therapy_sessions", "ix_therapy_sessions_deleted_at_partial"),
    ("notes", "ix_notes_deleted_at_partial"),
)


def upgrade() -> None:
    for table, index_name in _SOFT_DELETE_TARGETS:
        op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL")
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name} "
            f"ON {table} (deleted_at) "
            f"WHERE deleted_at IS NOT NULL"
        )


def downgrade() -> None:
    for table, index_name in _SOFT_DELETE_TARGETS:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS deleted_at")
