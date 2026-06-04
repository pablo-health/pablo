"""create patient medications table

Adds a ``patient_medications`` table for tracking per-patient medication
records — drug name, dose, status (active / discontinued / on_hold),
optional start/stop dates, and free-text notes.

The table is per-tenant (lives inside each practice_{id} schema, sibling
of ``notes``, ``outcome_measures``, and ``diagnostic_assessments``).
Access is enforced at the application layer via the same
``has_patient_access`` function used by those tables — no separate RLS
policy is added, matching the established notes/outcome-measures design.

Revision ID: 566ad1e08aa2
Revises: b2e7d4c91f05
Create Date: 2026-06-04
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "566ad1e08aa2"
down_revision: str | Sequence[str] | None = "b2e7d4c91f05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "patient_medications",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("drug_name", sa.String(length=200), nullable=False),
        sa.Column("dose", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.Date(), nullable=True),
        sa.Column("stopped_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','discontinued','on_hold')",
            name="ck_patient_medications_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_patient_medications_patient_id"),
        "patient_medications",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        "ix_patient_medications_patient_status",
        "patient_medications",
        ["patient_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patient_medications_patient_status",
        table_name="patient_medications",
    )
    op.drop_index(
        op.f("ix_patient_medications_patient_id"),
        table_name="patient_medications",
    )
    op.drop_table("patient_medications")
