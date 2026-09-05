# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""charge ledger: disputes and processor fees

Two additions to ``patient_charges``, landing together because the same
webhook receiver learns both from Stripe at the same time.

``disputed`` and ``dispute_lost`` join the status vocabulary. A dispute is not
a refund: the cardholder's bank has flagged the charge as contested, and
nothing has moved yet. It resolves either back to ``succeeded`` (the practice
keeps the money) or to ``dispute_lost`` (it does not) — collapsing that into
the existing ``refunded`` status would make a still-open dispute read as money
already returned, and would make a *won* dispute unrecoverable from
``refunded`` since nothing may currently un-refund a row.

``fee_cents`` and ``net_cents`` record what the processor's balance
transaction says it kept and paid out. Both start NULL and may stay NULL
indefinitely for a given charge — some payment methods settle the balance
transaction after the charge itself succeeds, so a charge can be genuinely
``succeeded`` with its fee still unknown.

Revision ID: 9b6f2c1a4d7e
Revises: c7a41d3e9b2c
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "9b6f2c1a4d7e"
down_revision: str | Sequence[str] | None = "c7a41d3e9b2c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_STATUSES = ("pending", "succeeded", "failed", "refunded")
_NEW_STATUSES = (*_OLD_STATUSES, "disputed", "dispute_lost")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column("patient_charges", sa.Column("fee_cents", sa.Integer(), nullable=True))
    op.add_column("patient_charges", sa.Column("net_cents", sa.Integer(), nullable=True))

    op.drop_constraint("ck_patient_charges_status", "patient_charges", type_="check")
    op.create_check_constraint(
        "ck_patient_charges_status",
        "patient_charges",
        f"status IN ({_in_list(_NEW_STATUSES)})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_patient_charges_status", "patient_charges", type_="check")
    op.create_check_constraint(
        "ck_patient_charges_status",
        "patient_charges",
        f"status IN ({_in_list(_OLD_STATUSES)})",
    )

    op.drop_column("patient_charges", "net_cents")
    op.drop_column("patient_charges", "fee_cents")
