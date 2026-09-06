# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""billing identity: practice profile, clinician taxonomy, patient address+sex

Filing a claim through a clearinghouse needs three things nothing in the
schema carried yet:

* The practice's own billing identity — legal name, tax id, billing NPI, and
  address — the "who is filing this claim" the payer needs on the claim
  header. ``practice_billing_profile`` is a singleton, same shape as
  ``scheduling_policy``: one row per practice, pinned by ``CHECK (id = 1)``,
  no ``user_id`` / ``patient_id`` to scope it by. RLS is left off for the
  same reason as that table (registered in ``not_row_scoped``): the
  isolation boundary is the tenant schema, not a per-row predicate.

  The tax id is the one genuinely sensitive value here, so it is encrypted
  at rest with the same AES-256-GCM helper already used for OAuth calendar
  tokens (``app.services.token_encryption``) rather than stored in the
  clear. ``tax_id_last4`` is a separate plaintext column so a settings page
  can show "···· 1234" without ever decrypting the real value.

* A taxonomy code on the clinician profile — the NUCC specialty
  classification a payer expects in the claim's rendering-provider loop,
  alongside the ``npi_number`` that already lives on this table.

* A mailing address and administrative sex on the patient — the X12 837P
  subscriber/patient loop needs both. ``sex`` is the X12 DMG03 code set
  (M/F/U), not a gender-identity field — the chart UI labels it "Sex on
  insurance card" to keep that distinction visible to whoever is filling
  it in.

Revision ID: e4f90a1bcb9e
Revises: 9b6f2c1a4d7e
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e4f90a1bcb9e"
down_revision: str | Sequence[str] | None = "9b6f2c1a4d7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "practice_billing_profile",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("legal_name", sa.String(length=255), nullable=True),
        sa.Column("tax_id_encrypted", sa.Text(), nullable=True),
        sa.Column("tax_id_last4", sa.String(length=4), nullable=True),
        sa.Column("tax_id_type", sa.String(length=3), nullable=True),
        sa.Column("billing_npi", sa.String(length=20), nullable=True),
        sa.Column("address_line1", sa.String(length=255), nullable=True),
        sa.Column("address_line2", sa.String(length=255), nullable=True),
        sa.Column("city", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("postal_code", sa.String(length=10), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_practice_billing_profile_singleton"),
        sa.CheckConstraint(
            "tax_id_type IN ('ein', 'ssn')", name="ck_practice_billing_profile_tax_id_type"
        ),
    )

    op.add_column(
        "clinician_profiles", sa.Column("taxonomy_code", sa.String(length=10), nullable=True)
    )

    op.add_column("patients", sa.Column("address_line1", sa.String(length=255), nullable=True))
    op.add_column("patients", sa.Column("address_line2", sa.String(length=255), nullable=True))
    op.add_column("patients", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column("patients", sa.Column("state", sa.String(length=2), nullable=True))
    op.add_column("patients", sa.Column("postal_code", sa.String(length=10), nullable=True))
    op.add_column("patients", sa.Column("sex", sa.String(length=1), nullable=True))
    op.create_check_constraint("ck_patients_sex", "patients", "sex IN ('M', 'F', 'U')")


def downgrade() -> None:
    op.drop_constraint("ck_patients_sex", "patients", type_="check")
    op.drop_column("patients", "sex")
    op.drop_column("patients", "postal_code")
    op.drop_column("patients", "state")
    op.drop_column("patients", "city")
    op.drop_column("patients", "address_line2")
    op.drop_column("patients", "address_line1")

    op.drop_column("clinician_profiles", "taxonomy_code")

    op.drop_table("practice_billing_profile")
