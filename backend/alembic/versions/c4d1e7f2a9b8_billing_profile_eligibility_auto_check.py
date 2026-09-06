# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""billing profile: eligibility_auto_check

Whether the practice wants an eligibility check run on its own whenever a
client's coverage lands — at intake, or when a plan is saved on the chart.
On by default, so a practice that has never opened its billing settings
still has the plan checked before the first session; off leaves only the
chart card's re-verify button. Lives on the billing profile singleton
because the check runs under the practice's billing identity.

Revision ID: c4d1e7f2a9b8
Revises: d7f2a9c4e8b1
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4d1e7f2a9b8"
down_revision: str | Sequence[str] | None = "d7f2a9c4e8b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "practice_billing_profile",
        sa.Column("eligibility_auto_check", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("practice_billing_profile", "eligibility_auto_check")
