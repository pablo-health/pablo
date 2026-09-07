# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""coverage copay override: what the practice collects at the door

An eligibility check answers what the payer knew when it was asked, and a
copay is the one figure on it a practice routinely knows better: the card
in front of them says $30, or their contract with the payer does. Without
somewhere to put that, the only place it could live is the clinician's
memory, retyped at every visit.

Nullable on purpose. NULL is "no override", which is a different statement
from "no copay" — with no override the stored 271 is the only answer there
is, and with neither the amount is asked for rather than guessed. The check
constraint keeps it a positive amount of money for the same reason the
charge ledger does: nothing collects zero.

Revision ID: a1f6c30b9d47
Revises: b8c1d47e2a95
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a1f6c30b9d47"
down_revision: str | Sequence[str] | None = "b8c1d47e2a95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "patient_coverage", sa.Column("copay_override_cents", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_patient_coverage_copay_override_positive",
        "patient_coverage",
        "copay_override_cents IS NULL OR copay_override_cents > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_patient_coverage_copay_override_positive", "patient_coverage", type_="check"
    )
    op.drop_column("patient_coverage", "copay_override_cents")
