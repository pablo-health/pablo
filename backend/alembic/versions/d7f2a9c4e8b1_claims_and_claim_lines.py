# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""claims and claim lines: a session turned into a claim

The chart already holds the visit codes, the rate, the coverage and the
practice's billing identity. Nothing held the claim itself — the record that
copies all of that at one moment, gets checked, and is what the payer sees.
Two tables.

``claims`` is the claim header: the practice's own control number (CLM01),
which client and which plan, where it stands, whether it is an original or a
replacement or a void of an earlier claim, the totals, the ordered diagnosis
list, and two snapshots — the billing identity and the subscriber as they
stood when the claim was built. Carries ``patient_id`` and no ``user_id``, so
``enable_rls_on_schema`` attaches the standard ``has_patient_access`` policy.

``claim_lines`` is one service line each: the code, modifiers, units, charge
and diagnosis pointers, with room for what the payer allowed and paid once a
remittance arrives. ``appointment_id`` is a soft reference (no foreign key):
appointments are deleted, money records are not. The line copies the claim's
``patient_id`` so the same row policy isolates it without a join.

Diagnosis codes, modifiers and pointers are JSON lists — the same shape the
appointment stores them in, and the shape every other list column in this
schema uses.

Revision ID: d7f2a9c4e8b1
Revises: b2c7e4a9d1f3
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d7f2a9c4e8b1"
down_revision: str | Sequence[str] | None = "b2c7e4a9d1f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("control_number", sa.String(length=17), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("coverage_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("payer_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("frequency_code", sa.String(length=1), nullable=False, server_default="1"),
        sa.Column("parent_claim_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("total_charge_cents", sa.Integer(), nullable=False),
        sa.Column("total_paid_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("diagnosis_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("place_of_service", sa.String(length=2), nullable=True),
        sa.Column("billing_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("subscriber_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payer_accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("adjudicated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_claims_patient_id_patients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["coverage_id"],
            ["patient_coverage.id"],
            name="fk_claims_coverage_id_patient_coverage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payer_id"],
            ["payers.id"],
            name="fk_claims_payer_id_payers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_claim_id"],
            ["claims.id"],
            name="fk_claims_parent_claim_id_claims",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'validated', 'submitted', 'ch_accepted', 'payer_accepted', "
            "'paid', 'partial', 'denied', 'rejected', 'stalled')",
            name="ck_claims_state",
        ),
        sa.CheckConstraint(
            "frequency_code IN ('1', '7', '8')",
            name="ck_claims_frequency_code",
        ),
        sa.CheckConstraint("total_charge_cents >= 0", name="ck_claims_total_charge_cents"),
        sa.CheckConstraint("total_paid_cents >= 0", name="ck_claims_total_paid_cents"),
        sa.UniqueConstraint("control_number", name="ux_claims_control_number"),
    )
    op.create_index("ix_claims_patient_id", "claims", ["patient_id"])
    op.create_index("ix_claims_state", "claims", ["state"])

    op.create_table(
        "claim_lines",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("claim_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("appointment_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("line_control_number", sa.String(length=30), nullable=False),
        sa.Column("service_date", sa.Date(), nullable=False),
        sa.Column("cpt", sa.String(length=10), nullable=False),
        sa.Column("modifiers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("charge_cents", sa.Integer(), nullable=False),
        sa.Column("dx_pointers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("telehealth", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_cents", sa.Integer(), nullable=True),
        sa.Column("paid_cents", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("patient_resp_cents", sa.Integer(), nullable=True),
        sa.Column("adjustments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_claim_lines_claim_id_claims",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_claim_lines_patient_id_patients",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("line_number > 0", name="ck_claim_lines_line_number"),
        sa.CheckConstraint("units > 0", name="ck_claim_lines_units"),
        sa.CheckConstraint("charge_cents >= 0", name="ck_claim_lines_charge_cents"),
        sa.UniqueConstraint("claim_id", "line_number", name="ux_claim_lines_claim_line_number"),
    )
    op.create_index("ix_claim_lines_claim_id", "claim_lines", ["claim_id"])
    op.create_index("ix_claim_lines_patient_id", "claim_lines", ["patient_id"])
    op.create_index("ix_claim_lines_appointment_id", "claim_lines", ["appointment_id"])


def downgrade() -> None:
    op.drop_index("ix_claim_lines_appointment_id", table_name="claim_lines")
    op.drop_index("ix_claim_lines_patient_id", table_name="claim_lines")
    op.drop_index("ix_claim_lines_claim_id", table_name="claim_lines")
    op.drop_table("claim_lines")
    op.drop_index("ix_claims_state", table_name="claims")
    op.drop_index("ix_claims_patient_id", table_name="claims")
    op.drop_table("claims")
