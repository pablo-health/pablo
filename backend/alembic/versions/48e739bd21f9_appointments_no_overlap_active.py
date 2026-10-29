# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointments: no two active appointments on one calendar may overlap

``uq_appointments_user_start_active`` (``2f1ec5526619``) stops two racing
bookings that share a clinician and a *start*. It says nothing about
10:00-10:50 against 10:30-11:20 on the same calendar: the service refuses
that with ``SchedulingService._reject_if_overlapping``, a check-then-insert
query, so two requests that both pass the check before either commits both
land, and the clinician is double-booked.

This adds the constraint a query cannot be: an exclusion constraint on
``(user_id, tstzrange(start_at, end_at))`` over every non-cancelled row.
Its scope is exactly ``PostgresAppointmentRepository.list_overlapping``'s —
same calendar (``user_id``), not cancelled, half-open interval ``[start,
end)`` so back-to-back is not a collision — because a constraint narrower
than the check would leave a race open and one wider would refuse bookings
the application accepts. Pending holds count, as they do everywhere else:
a requested slot is not offered to somebody else while it is being decided.

The loser of a race gets a constraint violation naming
``ex_appointments_user_time_active``, which the repository translates into
the same ``AppointmentConflictError`` the check raises, so the API answers
409 either way.

**Existing data.** A schema that already holds two overlapping non-cancelled
appointments on one calendar is a real double-booking sitting in the data,
and ``ADD CONSTRAINT`` would fail on it with a message naming only one row of
one pair. Postgres cannot add an exclusion constraint ``NOT VALID``, so this
revision looks first and refuses with every conflicting pair listed by id —
nothing to guess at, nothing silently skipped — so the operator can resolve
them (cancel one of each pair) and re-run. Rows whose ``end_at`` precedes
``start_at`` are listed for the same reason: ``tstzrange`` rejects them.

Revision ID: 48e739bd21f9
Revises: f4c8a1d6e3b9
Create Date: 2026-10-29
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "48e739bd21f9"
down_revision: str | Sequence[str] | None = "f4c8a1d6e3b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT_NAME = "ex_appointments_user_time_active"

_OVERLAPPING_PAIRS = text(
    """
    SELECT a.id, b.id
    FROM appointments a
    JOIN appointments b
      ON b.user_id = a.user_id
     AND b.id > a.id
     AND b.start_at < a.end_at
     AND b.end_at > a.start_at
    WHERE a.status <> 'cancelled' AND b.status <> 'cancelled'
    ORDER BY a.id, b.id
    """
)

_INVERTED_ROWS = text(
    "SELECT id FROM appointments WHERE end_at < start_at AND status <> 'cancelled' ORDER BY id"
)


def _refuse_if_already_double_booked() -> None:
    conn = op.get_bind()
    pairs = conn.execute(_OVERLAPPING_PAIRS).all()
    inverted = conn.execute(_INVERTED_ROWS).scalars().all()
    if not pairs and not inverted:
        return
    schema = conn.execute(text("SELECT current_schema()")).scalar()
    lines = [
        f"Cannot add {CONSTRAINT_NAME} to {schema}.appointments: "
        "the data already breaks it. Resolve each row below and re-run."
    ]
    lines.extend(f"  overlapping: {a} and {b}" for a, b in pairs)
    lines.extend(f"  end_at before start_at: {row_id}" for row_id in inverted)
    raise RuntimeError("\n".join(lines))


def _already_constrained() -> bool:
    return (
        op.get_bind()
        .execute(
            text(
                "SELECT 1 FROM pg_constraint "
                "WHERE conrelid = to_regclass('appointments') AND conname = :name"
            ),
            {"name": CONSTRAINT_NAME},
        )
        .first()
        is not None
    )


def upgrade() -> None:
    # A schema built from the template already has the constraint, and an
    # earlier revision's downgrade-and-replay runs this one over it again.
    if _already_constrained():
        return
    _refuse_if_already_double_booked()
    op.execute(
        f"ALTER TABLE appointments ADD CONSTRAINT {CONSTRAINT_NAME} "
        "EXCLUDE USING gist (user_id WITH =, tstzrange(start_at, end_at) WITH &&) "
        "WHERE (status <> 'cancelled')"
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE appointments DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
