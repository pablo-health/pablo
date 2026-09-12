# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""what a payer agreed to pay, so underpayment against it becomes visible

A practice can only tell it was underpaid if something records what it was
owed. Nothing did. The fee schedule arrives with the participation agreement,
after credentialing approval, and until now it lived in a PDF nobody could
compare a remittance against.

``contracted_rates`` is one row per code per contract period, hanging off the
``payer_participations`` row rather than off the payer — the rate belongs to
one clinician's contract with that payer, and in a group practice two
clinicians on the same payer can hold different schedules.

Versioned by ``effective_date`` and never edited in place: a schedule that
changes is a new row, so a claim from last year still reads against the rate in
force when it was filed. ``end_date`` NULL means still current.

Two bases, because fee schedules arrive in two shapes: a table of amounts per
code, or a percentage of the Medicare physician fee schedule for the locality.
The Medicare amount is a column rather than a lookup because this codebase
ships no fee schedule — the practice enters the locality amount from what the
payer supplied. A percentage row without it is an expected state, and the
variance says "not computable" rather than inventing a number somebody could
bill on.

``modifier`` is NOT NULL, defaulting to the empty string for an unmodified
code. Nullable would read better and break the unique constraint: NULLs are
distinct in Postgres, so two "no modifier" rates for one code and date would
both be accepted.

Carries ``user_id`` beside ``participation_id`` so the row takes its parent's
row-ownership policy without the policy engine learning a join — the same
shape ``payer_participation_events`` uses.

Revision ID: e1b7a3d95c48
Revises: d9a4c1e7b302
Create Date: 2026-09-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e1b7a3d95c48"
down_revision: str | Sequence[str] | None = "d9a4c1e7b302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "contracted_rates",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("participation_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("cpt", sa.String(length=10), nullable=False),
        sa.Column("modifier", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("basis", sa.String(length=16), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=True),
        sa.Column("percent", sa.Numeric(precision=7, scale=3), nullable=True),
        sa.Column("mpfs_amount_cents", sa.Integer(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("source_document_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_contracted_rates"),
        sa.ForeignKeyConstraint(
            ["participation_id"],
            ["payer_participations.id"],
            name="fk_contracted_rates_participation_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"],
            ["compliance_documents.id"],
            name="fk_contracted_rates_source_document_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "basis IN ('fixed', 'percent_of_mpfs')",
            name="ck_contracted_rates_basis",
        ),
        # Each basis carries its own number and not the other's, so no row is
        # ambiguous about which arm computed it.
        sa.CheckConstraint(
            "(basis = 'fixed' AND amount_cents IS NOT NULL AND percent IS NULL) OR "
            "(basis = 'percent_of_mpfs' AND percent IS NOT NULL AND amount_cents IS NULL)",
            name="ck_contracted_rates_basis_fields",
        ),
        sa.CheckConstraint(
            "amount_cents IS NULL OR amount_cents >= 0",
            name="ck_contracted_rates_amount",
        ),
        sa.CheckConstraint("percent IS NULL OR percent > 0", name="ck_contracted_rates_percent"),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= effective_date",
            name="ck_contracted_rates_date_order",
        ),
        sa.UniqueConstraint(
            "participation_id",
            "cpt",
            "modifier",
            "effective_date",
            name="ux_contracted_rates_participation_code_date",
        ),
    )
    op.create_index(
        "ix_contracted_rates_participation_id", "contracted_rates", ["participation_id"]
    )
    op.create_index("ix_contracted_rates_user_id", "contracted_rates", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_contracted_rates_user_id", table_name="contracted_rates")
    op.drop_index("ix_contracted_rates_participation_id", table_name="contracted_rates")
    op.drop_table("contracted_rates")
