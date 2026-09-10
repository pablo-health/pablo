# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""a claim receipt for what the payer decided

The receipt kinds so far record a claim leaving and being acknowledged —
submitted, ch_accepted, payer_accepted, rejected, stalled — and then stop.
Nothing records the payer saying what it actually did, because until now
nothing read a remittance.

One kind rather than three. Paid, partially paid and denied are the claim's
*state*, and the state column already records which one happened; what the
receipt adds is the amounts behind that move and the check or EFT trace that
ties them to money in the practice's account.

The kind list is a CHECK constraint rather than an enum type, so widening it
is a drop and recreate. Nothing is backfilled: no row can already carry a
kind this constraint did not permit.

Revision ID: b5d1c8e02f37
Revises: c3f7a1b90e42
Create Date: 2026-09-09
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b5d1c8e02f37"
down_revision: str | Sequence[str] | None = "c3f7a1b90e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_claim_events_kind"
_TABLE = "claim_events"

_KINDS_BEFORE = (
    "submitted",
    "ch_accepted",
    "payer_accepted",
    "rejected",
    "stalled",
    "acknowledged",
    "status_checked",
    "deadline_approaching",
    "deadline_missed",
)

_KINDS_AFTER = (*_KINDS_BEFORE, "adjudicated")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"kind IN ({_in_list(_KINDS_AFTER)})")


def downgrade() -> None:
    # A receipt of the new kind would fail the narrower constraint, so it has
    # to go before the constraint comes back. Dropping the row loses the
    # amounts it carried; the claim's own state and totals survive it.
    op.execute(f"DELETE FROM {_TABLE} WHERE kind = 'adjudicated'")  # noqa: S608 - no interpolation
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, f"kind IN ({_in_list(_KINDS_BEFORE)})")
