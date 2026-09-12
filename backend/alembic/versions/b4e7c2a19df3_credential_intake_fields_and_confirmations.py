# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""somewhere for the intake's answers to land, including the ones it confirms

The tiered intake asks for four facts the credential record had no column for,
and confirms fourteen more it has no way to record having confirmed.

The four columns go on ``credential_government_ids`` because that is already
the one-row-per-clinician table:

* ``supervision_status`` — the fork the whole question set branches on. It
  cannot live on ``supervision_relationships``, which is where a supervisor is
  named, because the intake asks this first, before there is a supervisor.
* ``caqh_id`` — pulled forward into the claims-ready tier deliberately: one
  field, and knowing it changes the later plan.
* ``medicare_intent`` / ``medicaid_intent`` — whether she wants the
  application filed at all. Distinct from enrollment, which is a
  ``payer_participations`` row with dates.

``credential_confirmations`` is the other half. The first tier asks nothing —
it fills from NPPES, the public PECOS file, the exclusion lists and what the
practice already stores, and asks only whether each value is right. Recording
that is not bookkeeping: a payer application separates self-reported data from
verified data, and "confirmed on this date against this source" is what moves
a value across that line.

Not a row in ``credential_disclosures``, whose check requires an explanation
when the answer is ``true``. That is right for an attestation and backwards
here — ``true`` means "correct" and needs nothing, while ``false`` is the
answer carrying a correction. The check here is that one's mirror image.

Carries ``user_id`` as its scoping column, so ``enable_rls_on_schema`` gives
it the ordinary row-ownership policy with no new branch.

Revision ID: b4e7c2a19df3
Revises: e1b7a3d95c48
Create Date: 2026-09-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b4e7c2a19df3"
down_revision: str | Sequence[str] | None = "e1b7a3d95c48"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "credential_government_ids",
        sa.Column("supervision_status", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "credential_government_ids",
        sa.Column("caqh_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "credential_government_ids",
        sa.Column("medicare_intent", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "credential_government_ids",
        sa.Column("medicaid_intent", sa.Boolean(), nullable=True),
    )
    op.create_check_constraint(
        "ck_credential_government_ids_supervision_status",
        "credential_government_ids",
        "supervision_status IS NULL OR supervision_status IN ('independent', 'supervised')",
    )

    op.create_table(
        "credential_confirmations",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("presented_value", sa.Text(), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column("correction", sa.Text(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_credential_confirmations"),
        sa.CheckConstraint(
            "source IN ('nppes', 'pecos_public_file', 'leie_sam', "
            "'clinician_profiles', 'practice_billing_profile')",
            name="ck_credential_confirmations_source",
        ),
        sa.CheckConstraint(
            "confirmed OR correction IS NOT NULL",
            name="ck_credential_confirmations_corrected",
        ),
        # Covers lookups by user_id on its leading column, so no separate index.
        sa.UniqueConstraint(
            "user_id",
            "field_key",
            name="ux_credential_confirmations_user_field",
        ),
    )


def downgrade() -> None:
    op.drop_table("credential_confirmations")
    op.drop_constraint(
        "ck_credential_government_ids_supervision_status",
        "credential_government_ids",
        type_="check",
    )
    op.drop_column("credential_government_ids", "medicaid_intent")
    op.drop_column("credential_government_ids", "medicare_intent")
    op.drop_column("credential_government_ids", "caqh_id")
    op.drop_column("credential_government_ids", "supervision_status")
