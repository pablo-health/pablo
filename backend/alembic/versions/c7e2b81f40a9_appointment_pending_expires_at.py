# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments.pending_expires_at — when a requested booking stops holding its slot

Request mode gives ``appointments.status`` a new value, ``pending``: a
booking that has been asked for but not yet agreed to by the practice. The
status column is already a free ``VARCHAR(20)`` with no check constraint, so
the value itself needs no DDL.

The expiry does. A pending appointment OCCUPIES ITS SLOT — availability
counts everything that is not cancelled as busy — so a request nobody gets
round to answering would hold a therapist's hour indefinitely, and a request
queue left unread would quietly eat the calendar. ``pending_expires_at`` is
the instant it stops holding, swept by
``SchedulingService.expire_pending_appointments``.

The column is deliberately not computed here. How long a practice is willing
to sit on a request, and how much notice it wants, are decisions belonging to
whichever surface took the booking; this column just stores the answer.

NULL for every existing row and for every status other than ``pending``, so
nothing already in the table changes meaning.

Revision ID: c7e2b81f40a9
Revises: b3a7f92c15e4
Create Date: 2026-08-26
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c7e2b81f40a9"
down_revision: str | Sequence[str] | None = "b3a7f92c15e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE appointments ADD COLUMN IF NOT EXISTS pending_expires_at TIMESTAMPTZ NULL"
    )
    # The sweep is a range scan over pending rows, and it runs per practice on
    # a schedule; partial so it costs nothing on the confirmed rows that are
    # almost all of the table.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_appointments_pending_expires_at "
        "ON appointments (pending_expires_at) "
        "WHERE pending_expires_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_appointments_pending_expires_at")
    op.execute("ALTER TABLE appointments DROP COLUMN IF EXISTS pending_expires_at")
