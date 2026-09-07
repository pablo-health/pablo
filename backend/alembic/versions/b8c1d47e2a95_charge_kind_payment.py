# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""a ledger row can be a payment against a bill another row raised

``e4a7b2c9d013`` gave the charge ledger its kinds and constrained them to
six. That set is missing the one a client needs in order to pay down a
balance: money collected against a bill somebody ELSE raised. A ``session``
charge cannot do that job, because a session row is itself a bill — settling
a ``patient_resp`` with one re-bills the very amount it was paying off — so
the ledger needs a seventh kind, ``payment``.

This is a separate revision rather than an edit to ``e4a7b2c9d013`` because
that revision has already shipped. Alembic never re-runs a revision a
database is stamped at, so widening the constraint in place would have been
invisible where it mattered: a freshly built database (every CI run) would
apply the edited version and go green, while every already-migrated
environment kept the six-kind constraint and failed on the first inserted
``payment`` row — a CHECK violation on the route that moves money, with CI
reporting green throughout. The same split would have hit tenants, since
provisioning applies ``tenant_template.sql`` while existing tenants follow
the chain.

Revision ID: b8c1d47e2a95
Revises: e4a7b2c9d013
Create Date: 2026-09-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b8c1d47e2a95"
down_revision: str | Sequence[str] | None = "e4a7b2c9d013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_patient_charges_kind"

_KINDS_WITH_PAYMENT = (
    "session",
    "copay",
    "payment",
    "patient_resp",
    "contractual_adjustment",
    "write_off",
    "credit",
)

#: What ``e4a7b2c9d013`` constrained the column to, restored on downgrade.
_KINDS_WITHOUT_PAYMENT = tuple(kind for kind in _KINDS_WITH_PAYMENT if kind != "payment")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _recreate(kinds: tuple[str, ...]) -> None:
    op.drop_constraint(_CONSTRAINT, "patient_charges", type_="check")
    op.create_check_constraint(_CONSTRAINT, "patient_charges", f"kind IN ({_in_list(kinds)})")


def upgrade() -> None:
    _recreate(_KINDS_WITH_PAYMENT)


def downgrade() -> None:
    # Narrowing the constraint fails outright if any payment row exists, which
    # is the correct outcome: the alternative is silently deleting somebody's
    # record of money they collected.
    _recreate(_KINDS_WITHOUT_PAYMENT)
