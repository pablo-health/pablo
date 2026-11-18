# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rows naming a client go with the client: patient_id keys on seven more tables

Seven per-tenant tables name a client in a ``patient_id`` uuid with no foreign
key to ``patients``, so deleting the client's ``patients`` row never reaches
them. This adds ``FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE
CASCADE`` to each, named ``<table>_patient_id_fkey`` like the existing ones,
and indexes ``external_calendar_events.patient_id`` so the cascade does not
scan that table. Nothing is deleted. A row naming a missing client fails the
migration, by table, and the tenant's upgrade rolls back whole.

Adding a key checks every existing row, as the migration role. Under FORCE
ROW LEVEL SECURITY that role sees no rows, so the check would be wrong both
ways: a healthy table pointing at ``patients`` fails, and a forced table with
an orphan in it passes. FORCE is lifted on the tables involved for the adds
and put back on exactly the ones that had it. Policies are not touched.

Revision ID: 8c5e65a0e56a
Revises: 16d6543b3c6f
Create Date: 2026-11-17
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "8c5e65a0e56a"
down_revision: str | Sequence[str] | None = "16d6543b3c6f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "patient_intake_assignments",
    "patient_intake_submissions",
    "patient_charges",
    "patient_payment_methods",
    "companion_sessions",
    "companion_auth_challenges",
    "external_calendar_events",
)
_INDEX = "ix_external_calendar_events_patient_id"


def upgrade() -> None:
    # search_path is set to the tenant schema by the runner.
    conn = op.get_bind()
    forced = (
        conn.execute(
            sa.text(
                "SELECT c.relname FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = current_schema() AND c.relforcerowsecurity"
                " AND c.relname = ANY(:tables)"
            ),
            {"tables": ["patients", *_TABLES]},
        )
        .scalars()
        .all()
    )
    existing = set(
        conn.execute(
            sa.text(
                "SELECT conname FROM pg_constraint"
                " WHERE connamespace = current_schema()::regnamespace"
            )
        ).scalars()
    )

    for table in forced:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    for table in _TABLES:
        if f"{table}_patient_id_fkey" in existing:
            continue
        try:
            op.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {table}_patient_id_fkey"
                " FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE"
            )
        except sa.exc.IntegrityError:
            # Postgres's own message carries the orphan's patient_id; this one
            # names only the table.
            msg = f"{table} has rows whose patient_id names no client; delete them and re-run"
            raise RuntimeError(msg) from None
    for table in forced:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(f"CREATE INDEX IF NOT EXISTS {_INDEX} ON external_calendar_events (patient_id)")


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    for table in reversed(_TABLES):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {table}_patient_id_fkey")
