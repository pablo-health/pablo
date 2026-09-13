# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""payers: which transactions the practice wants enrolled

Three switches per payer. Eligibility and claims default on; remittance
defaults off, because completing a remittance enrollment moves the payer's
ERAs to us from wherever they arrive today, and a practice with a billing
service downstream should be asked before that happens.

A payer that already has an 835 request on file is backfilled to on. The
default describes a payer nobody has decided about yet; one that was already
enrolled for remittance has been decided about, and writing the default over
it would say the practice wants something it already has.

Revision ID: c2f8b40d97ae
Revises: a7d4e91c3b62
Create Date: 2026-09-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c2f8b40d97ae"
down_revision: str | Sequence[str] | None = "a7d4e91c3b62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "payers",
        sa.Column("enroll_eligibility", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "payers", sa.Column("enroll_claims", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "payers",
        sa.Column("enroll_remittance", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE payers SET enroll_remittance = true
        WHERE id IN (
            SELECT payer_id FROM payer_enrollments WHERE transaction_type = '835'
        )
        """
    )


def downgrade() -> None:
    op.drop_column("payers", "enroll_remittance")
    op.drop_column("payers", "enroll_claims")
    op.drop_column("payers", "enroll_eligibility")
