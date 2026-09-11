# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""At most one balance payment in flight per client

``POST /api/patients/{id}/charge-balance`` refuses when a pending ``payment``
row already exists, but that is a read followed by an insert: two requests can
both read the ledger before either has written. A staged row is ``pending``,
and ``pending`` is deliberately not a status the balance counts as collected —
so while the first request is at the processor the balance still reads as
owed, the second passes the same check, and the card is charged again for the
whole of it. The client ends up with two charges and a refund somebody has to
notice.

The route's check stays, as the fast path that gives a useful 409 without a
round trip to the constraint. This is what makes it true.

Partial on both columns, and both halves matter:

* ``kind = 'payment'`` — every other kind is a BILL rather than a collection,
  and a client may legitimately have any number outstanding. Constraining
  them would refuse a second session charge for no reason.
* ``status = 'pending'`` — a terminal row must never block. A decline is
  final, and retrying is a fresh charge a clinician asked for.

Written ``IF NOT EXISTS`` and non-concurrently. A tenant schema is small and
this runs inside the migration's transaction like everything else in the
chain; ``CONCURRENTLY`` cannot run in one and would need its own connection
handling for no benefit at this size.

**Existing data may already violate it.** A tenant that accumulated more than
one stranded ``pending`` payment for a client cannot take this index, and the
migration will fail loudly rather than pick a row to discard — which row was
collected is a question about somebody's money and belongs to a human with
the processor's dashboard open, not to a migration.

Revision ID: b8f43d2e17c0
Revises: a3e71c920d64
Create Date: 2026-09-11
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b8f43d2e17c0"
down_revision: str | Sequence[str] | None = "a3e71c920d64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ux_patient_charges_one_pending_payment"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX}
            ON patient_charges (patient_id)
            WHERE kind = 'payment' AND status = 'pending';
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {_INDEX};")
