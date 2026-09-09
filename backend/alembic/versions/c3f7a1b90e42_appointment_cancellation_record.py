# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Record who cancelled an appointment, when, and whether it was late.

A practice's cancellation notice period is a FEE boundary, not a permission
one. Anybody may cancel at any time — the alternative to a late cancellation
is a no-show, which costs the practice the slot AND the warning AND any chance
of filling it. These columns are what make the resulting fee defensible.

Nothing existing could carry that. ``updated_at`` moves on any later edit, so
by billing time it may say nothing about when the slot was given up; and with
no actor recorded, a lapsed hold, a clinician rearranging their own week, and
a patient cancelling an hour beforehand are the same row — only one of which
anybody may be charged for.

All four are nullable with no backfill. A row cancelled before this shipped
reads as "not known", which is true, rather than being defaulted into a claim
that the cancellation was early, on time, or nobody's doing.

Revision ID: c3f7a1b90e42
Revises: e2b7c4f19d38
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3f7a1b90e42"
down_revision = "e2b7c4f19d38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "appointments",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("appointments", sa.Column("cancelled_by", sa.String(length=20), nullable=True))
    op.add_column(
        "appointments",
        sa.Column("cancelled_by_id", sa.Uuid(as_uuid=False), nullable=True),
    )
    op.add_column("appointments", sa.Column("late_cancellation", sa.Boolean(), nullable=True))

    # A reschedule leaves two rows — the old one cancelled, a new one at the
    # new time — so the slot given up survives instead of being overwritten.
    # Set means the cancellation was a move; NULL means it was outright.
    op.add_column(
        "appointments", sa.Column("superseded_by_id", sa.Uuid(as_uuid=False), nullable=True)
    )

    # The caller's attestation that it was told the change fell inside the
    # notice period. The API refuses a late change without it.
    op.add_column(
        "appointments", sa.Column("late_change_acknowledged", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("appointments", "late_change_acknowledged")
    op.drop_column("appointments", "superseded_by_id")
    op.drop_column("appointments", "late_cancellation")
    op.drop_column("appointments", "cancelled_by_id")
    op.drop_column("appointments", "cancelled_by")
    op.drop_column("appointments", "cancelled_at")
