# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""charge ledger kinds: what a row IS, and what it belongs to

The ledger only ever held one kind of row — the full-rate charge for a
visit — so it needed no column saying which kind it was. Insurance makes
that insufficient: to answer what a client owes after the payer has paid,
a row has to say whether it is the visit charge, the copay taken at the
door, the client's share from the remittance, the contractual adjustment
nobody owes, a write-off, or a credit.

``kind`` therefore arrives with a server-side default of ``session``, which
is what makes the backfill a no-op: every row that predates this column is
a full-rate session charge, and reads as one without an UPDATE.

``claim_id`` links the rows a remittance writes back to the claim that
produced them, ``ON DELETE SET NULL`` so a deleted claim cannot take a
money record with it. ``write_off_reason`` is required on a write-off and
forbidden elsewhere — both halves, because an unexplained write-off is the
row nobody can account for later and a reason on a copay means the kind is
wrong. ``settled_by_charge_id`` is how an owed row stops being owed when a
separate charge collects it; it is a soft reference to another row of this
same table, with no foreign key because the two rows are written in either
order depending on how the money arrived.

No behaviour changes here. This is the schema the copay, balance,
statement, remittance and write-off work all read.

Revision ID: e4a7b2c9d013
Revises: d7e2f4a6b8c1
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e4a7b2c9d013"
down_revision: str | Sequence[str] | None = "d7e2f4a6b8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHARGE_KINDS = (
    "session",
    "copay",
    "payment",
    "patient_resp",
    "contractual_adjustment",
    "write_off",
    "credit",
)

_WRITE_OFF_REASONS = ("hardship", "small_balance", "courtesy", "error")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column(
        "patient_charges",
        sa.Column("kind", sa.String(length=24), nullable=False, server_default="session"),
    )
    op.add_column("patient_charges", sa.Column("claim_id", sa.Uuid(as_uuid=False), nullable=True))
    op.add_column(
        "patient_charges", sa.Column("write_off_reason", sa.String(length=24), nullable=True)
    )
    op.add_column("patient_charges", sa.Column("note", sa.Text(), nullable=True))
    op.add_column(
        "patient_charges", sa.Column("settled_by_charge_id", sa.String(length=128), nullable=True)
    )

    op.create_foreign_key(
        "fk_patient_charges_claim_id_claims",
        "patient_charges",
        "claims",
        ["claim_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_patient_charges_kind",
        "patient_charges",
        f"kind IN ({_in_list(_CHARGE_KINDS)})",
    )
    op.create_check_constraint(
        "ck_patient_charges_write_off_reason_kind",
        "patient_charges",
        "(kind = 'write_off') = (write_off_reason IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_patient_charges_write_off_reason",
        "patient_charges",
        f"write_off_reason IS NULL OR write_off_reason IN ({_in_list(_WRITE_OFF_REASONS)})",
    )
    op.create_index(
        "ix_patient_charges_claim_id",
        "patient_charges",
        ["claim_id"],
        postgresql_where=sa.text("claim_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_patient_charges_claim_id", table_name="patient_charges")
    op.drop_constraint("ck_patient_charges_write_off_reason", "patient_charges", type_="check")
    op.drop_constraint("ck_patient_charges_write_off_reason_kind", "patient_charges", type_="check")
    op.drop_constraint("ck_patient_charges_kind", "patient_charges", type_="check")
    op.drop_constraint("fk_patient_charges_claim_id_claims", "patient_charges", type_="foreignkey")
    for column in ("settled_by_charge_id", "note", "write_off_reason", "claim_id", "kind"):
        op.drop_column("patient_charges", column)
