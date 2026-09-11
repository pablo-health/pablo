# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""billing profile: write-off policy

Two practice-level switches a write-off route checks before it will let a
clinician give money away: whether courtesy waivers are allowed at all
(``allow_courtesy_writeoffs``, off by default — a practice opts in), and the
balance at or under which a "not worth chasing" write-off is allowed
(``small_balance_cents``, defaulted to $5.00). Lives on the billing profile
singleton for the same reason ``eligibility_auto_check`` does: it is
practice-wide policy, not a per-client or per-user setting.

Revision ID: f6a3c9d21b47
Revises: b5d1c8e02f37
Create Date: 2026-09-10
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f6a3c9d21b47"
down_revision: str | Sequence[str] | None = "b5d1c8e02f37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "practice_billing_profile",
        sa.Column(
            "allow_courtesy_writeoffs", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "practice_billing_profile",
        sa.Column("small_balance_cents", sa.Integer(), nullable=False, server_default="500"),
    )
    op.create_check_constraint(
        "ck_practice_billing_profile_small_balance",
        "practice_billing_profile",
        "small_balance_cents >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_practice_billing_profile_small_balance", "practice_billing_profile", type_="check"
    )
    op.drop_column("practice_billing_profile", "small_balance_cents")
    op.drop_column("practice_billing_profile", "allow_courtesy_writeoffs")
