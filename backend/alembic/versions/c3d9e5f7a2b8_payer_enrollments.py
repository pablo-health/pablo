# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""payer enrollments and the billing profile's clearinghouse identity

Before a payer accepts a practice's claims or returns remittances
electronically, the practice has to be enrolled with that payer through its
clearinghouse account: one request per transaction type, and remittance
always needs one. Nothing in the schema recorded those requests.

``payer_enrollments`` is one row per payer per transaction type (837P, 270,
835): the clearinghouse's id for the request, where it stands, and the
clearinghouse's own wording of what the payer needs when it is waiting on
the practice. Practice-level like ``payers`` — the practice is enrolled, not
a clinician — so it carries no ``user_id`` / ``patient_id`` and, keyed by
``(payer_id, transaction_type)``, no ``id`` either; the tenant schema is its
isolation boundary and ``enable_rls_on_schema`` never considers it.

``practice_billing_profile`` gains the two things the clearinghouse needs to
know the practice by: the provider record's id once one has been created,
and the practice's general contact inbox, which is the address every
enrollment request names (never an individual clinician's).

Revision ID: c3d9e5f7a2b8
Revises: d7f2a9c4e8b1
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c3d9e5f7a2b8"
down_revision: str | Sequence[str] | None = "d7f2a9c4e8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "practice_billing_profile",
        sa.Column("contact_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "practice_billing_profile",
        sa.Column("clearinghouse_provider_id", sa.String(length=80), nullable=True),
    )

    op.create_table(
        "payer_enrollments",
        sa.Column("payer_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("transaction_type", sa.String(length=4), nullable=False),
        sa.Column("vendor_request_id", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("payer_id", "transaction_type", name="pk_payer_enrollments"),
        sa.ForeignKeyConstraint(
            ["payer_id"],
            ["payers.id"],
            name="fk_payer_enrollments_payer_id_payers",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "transaction_type IN ('837P', '270', '835')",
            name="ck_payer_enrollments_transaction_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'stedi_action_required', 'provider_action_required', "
            "'provisioning', 'live', 'rejected', 'canceled')",
            name="ck_payer_enrollments_status",
        ),
    )
    op.create_index(
        "ix_payer_enrollments_vendor_request_id", "payer_enrollments", ["vendor_request_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_payer_enrollments_vendor_request_id", table_name="payer_enrollments")
    op.drop_table("payer_enrollments")
    op.drop_column("practice_billing_profile", "clearinghouse_provider_id")
    op.drop_column("practice_billing_profile", "contact_email")
