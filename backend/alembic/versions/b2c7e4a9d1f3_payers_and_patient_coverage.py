# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""coverage on file: payers and patient_coverage

Nothing in the schema recorded which plan a client is on. A claim and an
eligibility check both need it, and the client already types it from their
card at intake. Two tables.

``payers`` is the practice's list of insurance payers: the electronic payer id
from the card or the payer directory, the clearinghouse's own id once one has
been looked up, whether the payer is a behavioral-health carve-out of another,
and where the practice stands with it for electronic transactions. It also
carries the three deadlines a claim against the payer lives under (timely
filing, corrected claim, appeal) so the claim workflow can read them from the
row rather than a constant. Practice-level, no ``user_id`` / ``patient_id``;
RLS is deliberately left off (``_CORE_NOT_ROW_SCOPED``), the same posture as
``practice_billing_profile``.

``patient_coverage`` is one client's plan: the payer, member and group ids,
who the subscriber is, and the subscriber's own details when that is somebody
other than the client. It carries ``patient_id`` and no ``user_id``, so
``enable_rls_on_schema`` attaches the standard ``has_patient_access`` policy.
One active primary coverage per client, enforced by a partial unique index;
replacing a plan deactivates the old row rather than deleting it.

Revision ID: b2c7e4a9d1f3
Revises: e4f90a1bcb9e
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b2c7e4a9d1f3"
down_revision: str | Sequence[str] | None = "e4f90a1bcb9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payers",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("payer_id", sa.String(length=80), nullable=False),
        sa.Column("clearinghouse_payer_id", sa.String(length=80), nullable=True),
        sa.Column("is_carveout", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("carveout_of", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("enrollment_status", sa.String(length=16), nullable=False, server_default="none"),
        sa.Column("timely_filing_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("corrected_claim_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("appeal_days", sa.Integer(), nullable=False, server_default="180"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["carveout_of"],
            ["payers.id"],
            name="fk_payers_carveout_of_payers",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "enrollment_status IN ('none', 'filed', 'pending', 'active', 'error')",
            name="ck_payers_enrollment_status",
        ),
        sa.CheckConstraint("timely_filing_days > 0", name="ck_payers_timely_filing_days"),
        sa.CheckConstraint("corrected_claim_days > 0", name="ck_payers_corrected_claim_days"),
        sa.CheckConstraint("appeal_days > 0", name="ck_payers_appeal_days"),
    )
    op.create_index("ix_payers_payer_id", "payers", ["payer_id"])

    op.create_table(
        "patient_coverage",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("payer_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("member_id", sa.String(length=80), nullable=False),
        sa.Column("group_number", sa.String(length=80), nullable=True),
        sa.Column(
            "subscriber_relationship",
            sa.String(length=10),
            nullable=False,
            server_default="self",
        ),
        sa.Column("subscriber_first_name", sa.String(length=255), nullable=True),
        sa.Column("subscriber_last_name", sa.String(length=255), nullable=True),
        sa.Column("subscriber_date_of_birth", sa.Date(), nullable=True),
        sa.Column("subscriber_sex", sa.String(length=1), nullable=True),
        sa.Column("subscriber_address_line1", sa.String(length=255), nullable=True),
        sa.Column("subscriber_address_line2", sa.String(length=255), nullable=True),
        sa.Column("subscriber_city", sa.String(length=100), nullable=True),
        sa.Column("subscriber_state", sa.String(length=2), nullable=True),
        sa.Column("subscriber_postal_code", sa.String(length=10), nullable=True),
        sa.Column("plan_name", sa.String(length=255), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_271", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_patient_coverage_patient_id_patients",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["payer_id"],
            ["payers.id"],
            name="fk_patient_coverage_payer_id_payers",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "subscriber_relationship IN ('self', 'spouse', 'child', 'other')",
            name="ck_patient_coverage_subscriber_relationship",
        ),
        sa.CheckConstraint(
            "subscriber_sex IS NULL OR subscriber_sex IN ('M', 'F', 'U')",
            name="ck_patient_coverage_subscriber_sex",
        ),
    )
    # One active primary coverage per client. Partial, so the deactivated
    # history a plan change leaves behind does not collide with the new row.
    op.create_index(
        "ux_patient_coverage_active_primary",
        "patient_coverage",
        ["patient_id"],
        unique=True,
        postgresql_where=sa.text("active"),
    )


def downgrade() -> None:
    op.drop_index("ux_patient_coverage_active_primary", table_name="patient_coverage")
    op.drop_table("patient_coverage")
    op.drop_index("ix_payers_payer_id", table_name="payers")
    op.drop_table("payers")
