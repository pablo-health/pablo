"""create outcome measures table

Adds a first-class ``outcome_measures`` table for storing scored clinical
instrument results (PHQ-9, GAD-7, and any future generic instrument).

The table is per-tenant (lives inside each practice_{id} schema, sibling
of ``notes`` and ``patients``).  Access is enforced at the application
layer via the same ``has_patient_access`` function used by ``notes`` —
no separate RLS policy is added, matching the notes design.

See PABLO-o5k.

Revision ID: 5740d08f0503
Revises: c3f8b1d92e47
Create Date: 2026-06-02
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "5740d08f0503"
down_revision: str | Sequence[str] | None = "c3f8b1d92e47"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outcome_measures",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("appointment_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("instrument", sa.String(length=20), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=True),
        sa.Column(
            "item_scores",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "is_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column(
            "item_citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("administered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source IN ('patient_self_report','clinician_administered_verbal','manual','inferred')",
            name="ck_outcome_measures_source",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_outcome_measures_patient_id"),
        "outcome_measures",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outcome_measures_session_id"),
        "outcome_measures",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_outcome_measures_appointment_id"),
        "outcome_measures",
        ["appointment_id"],
        unique=False,
    )
    op.create_index(
        "ix_outcome_measures_patient_instrument_administered",
        "outcome_measures",
        ["patient_id", "instrument", "administered_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outcome_measures_patient_instrument_administered",
        table_name="outcome_measures",
    )
    op.drop_index(
        op.f("ix_outcome_measures_appointment_id"),
        table_name="outcome_measures",
    )
    op.drop_index(
        op.f("ix_outcome_measures_session_id"),
        table_name="outcome_measures",
    )
    op.drop_index(
        op.f("ix_outcome_measures_patient_id"),
        table_name="outcome_measures",
    )
    op.drop_table("outcome_measures")
