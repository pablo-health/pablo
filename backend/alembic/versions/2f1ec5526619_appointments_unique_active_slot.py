# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments: unique active slot per clinician

The scheduling engine already refuses an overlapping booking with a
check-then-insert query (``SchedulingService._reject_if_overlapping``),
but that check and the insert it guards are two separate statements —
two requests racing for the same free slot can both pass the check
before either has committed, and both insert. The public booking
surface is the likeliest place for that race: two visitors hitting
"book" on the same slot within the same instant, with no coordination
between their requests.

This adds the backstop a query can't give: a partial unique index on
``(user_id, start_at)`` covering every non-cancelled row. A cancelled
appointment never blocks a slot from being rebooked, but a pending
request does — it's already holding the slot against everyone else,
which is exactly what lets a booking link's "reserve while you confirm
by email" flow work. Two racing inserts for the same clinician and
start time now can't both land: the loser gets a constraint violation
instead of a phantom double-booking, and the route above translates
that into the same "slot taken" response the check-then-insert path
already gives a slower loser.

Deliberately not "CREATE UNIQUE INDEX ... IF NOT EXISTS" for the create
step: if a schema already has two non-cancelled appointments sharing a
clinician and start time, that's a real double-booking sitting in the
data, and this migration should fail loudly and stop rather than
silently skip adding the constraint or, worse, guess which row to keep.

Revision ID: 2f1ec5526619
Revises: e480337eb6bd
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "2f1ec5526619"
down_revision: str | Sequence[str] | None = "e480337eb6bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_appointments_user_start_active "
        "ON appointments (user_id, start_at) "
        "WHERE status <> 'cancelled'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_appointments_user_start_active")
