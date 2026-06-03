"""create diagnostic assessments table

Adds the per-tenant ``diagnostic_assessments`` table for structured diagnostic
determinations (criterion counts + gates -> diagnosis + ICD-10-CM code),
sibling of ``outcome_measures`` and ``notes`` inside each ``practice_{id}``
schema. Access is enforced at the application layer via ``has_patient_access``
(same as notes / outcome_measures) — no separate RLS policy.

The platform-schema reference tables (``icd10_codes``,
``diagnostic_definitions``) are created via ``PlatformBase.metadata.create_all``
at bootstrap, not here.

See PABLO-6xj.

Revision ID: b7e2f4a1c9d3
Revises: 5740d08f0503
Create Date: 2026-06-02
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b7e2f4a1c9d3"
down_revision: str | Sequence[str] | None = "5740d08f0503"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diagnostic_assessments",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("session_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("appointment_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("instrument", sa.String(length=40), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column(
            "criterion_responses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "gate_responses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("meets_criteria", sa.Boolean(), nullable=False),
        sa.Column("determined_icd10", sa.String(length=10), nullable=True),
        sa.Column("diagnosis_label", sa.String(length=120), nullable=True),
        sa.Column(
            "criterion_citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source IN ('patient_self_report','clinician_administered_verbal','manual','inferred')",
            name="ck_diagnostic_assessments_source",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_diagnostic_assessments_patient_id"),
        "diagnostic_assessments",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_diagnostic_assessments_session_id"),
        "diagnostic_assessments",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_diagnostic_assessments_appointment_id"),
        "diagnostic_assessments",
        ["appointment_id"],
        unique=False,
    )
    op.create_index(
        "ix_diagnostic_assessments_patient_instrument_assessed",
        "diagnostic_assessments",
        ["patient_id", "instrument", "assessed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_diagnostic_assessments_patient_instrument_assessed",
        table_name="diagnostic_assessments",
    )
    op.drop_index(
        op.f("ix_diagnostic_assessments_appointment_id"),
        table_name="diagnostic_assessments",
    )
    op.drop_index(
        op.f("ix_diagnostic_assessments_session_id"),
        table_name="diagnostic_assessments",
    )
    op.drop_index(
        op.f("ix_diagnostic_assessments_patient_id"),
        table_name="diagnostic_assessments",
    )
    op.drop_table("diagnostic_assessments")
