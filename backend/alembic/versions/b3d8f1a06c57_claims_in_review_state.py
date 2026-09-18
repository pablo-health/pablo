# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""claims: allow the in_review state

``claims.state`` is a VARCHAR guarded by a CHECK constraint rather than a
native enum (see ``d7f2a9c4e8b1``), so admitting a new state is a constraint
swap and not an ``ALTER TYPE``. ``in_review`` is the deliberate hold before
filing: a deployment can ask for the first claim it sends a payer to be read
by a person before it goes out, because everything payer-specific is unproven
until a claim actually goes and a mistake comes back as a denial days later.

The constraint is what makes this a migration rather than a Python-only
change. ``app.db.models.CLAIM_STATES`` builds the constraint text, so the
model and the database disagree until this runs, and the disagreement surfaces
as an ``IntegrityError`` at the moment a claim first tries to enter the state —
far from the change that caused it.

Dropped and re-added rather than edited in place: Postgres has no
"alter check constraint", and the drop/add pair inside one transaction is
atomic, so no window exists where the column is unguarded.

Idempotent both ways (``IF EXISTS`` / name check), matching the tenant
migrations around it, because this chain is re-applied across every practice
schema and a fresh schema may already carry the constraint from the template.

Widening only: every state that was legal stays legal, so there is nothing to
backfill and no row can be left failing the new constraint.

Revision ID: b3d8f1a06c57
Revises: 94a180c79544
Create Date: 2026-09-18
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b3d8f1a06c57"
down_revision: str | Sequence[str] | None = "94a180c79544"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept as literal text rather than imported from ``app.db.models``: a
#: migration states what the schema looked like at this revision, and one that
#: reads the live model would silently change meaning the next time the model
#: does.
_STATES_WITH_REVIEW = (
    "'draft', 'validated', 'in_review', 'submitted', 'ch_accepted', "
    "'payer_accepted', 'paid', 'partial', 'denied', 'rejected', 'stalled'"
)
_STATES_WITHOUT_REVIEW = (
    "'draft', 'validated', 'submitted', 'ch_accepted', 'payer_accepted', "
    "'paid', 'partial', 'denied', 'rejected', 'stalled'"
)


def _swap_state_constraint(states: str) -> None:
    """Re-point ``ck_claims_state`` at ``states``, unqualified and idempotent.

    Unqualified on purpose: the tenant chain runs once per practice schema
    with ``search_path`` bound to it, so naming a schema here would send every
    tenant's DDL at one of them.
    """
    op.execute("ALTER TABLE claims DROP CONSTRAINT IF EXISTS ck_claims_state")
    op.execute(f"ALTER TABLE claims ADD CONSTRAINT ck_claims_state CHECK (state IN ({states}))")


def upgrade() -> None:
    _swap_state_constraint(_STATES_WITH_REVIEW)


def downgrade() -> None:
    # A claim sitting in `in_review` would fail the narrowed constraint, and
    # failing a downgrade on real data is worse than landing it somewhere
    # legal. `validated` is where such a claim came from and where approving
    # it would have returned it, so the hold is released rather than lost.
    op.execute("UPDATE claims SET state = 'validated' WHERE state = 'in_review'")
    _swap_state_constraint(_STATES_WITHOUT_REVIEW)
