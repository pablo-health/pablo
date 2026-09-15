# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Record how a client's money arrived, not only that a card was charged

The ledger could describe one way of being paid. A practice that takes a
cheque, cash, or a bank transfer had no way to record the payment at all, so
the client's balance never cleared and the statement that reads it said they
had paid nothing.

Two columns. ``method`` is how the money arrived; ``payment_reference`` is how
the practice finds it again in its own records — a cheque number, a transfer
date. The reference is deliberately not ``note``: ``note`` is practice-private
prose that a clinician will write clinical context into, and this one appears
on a statement a client may hand to a payer.

THE BACKFILL IS TRUE, NOT ASSUMED. Every existing row of a collecting kind was
written by ``stage_charge``, which exists only to call the processor;
``add_ledger_row``, the one writer that never touches a processor, is called in
exactly three places and writes ``patient_resp`` and ``write_off``, neither of
which collects. So ``card`` is what those rows have always been, and the
constraint can be added in the same pass rather than after a grace period.

Ordering matters here: the backfill runs BEFORE the kind/method constraint, or
the constraint rejects every row already in the table.

Revision ID: c1b7e4a92d53
Revises: e3a9c7b1d802
Create Date: 2026-09-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "c1b7e4a92d53"
down_revision: str | Sequence[str] | None = "e3a9c7b1d802"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Kept as literals rather than imported from ``app.db.models``. A migration
#: describes the schema at one moment; importing the live tuples would make an
#: applied migration change meaning the next time somebody edits the model.
_METHODS = ("card", "cash", "check", "other")
_COLLECTING_KINDS = ("session", "copay", "payment")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.add_column("patient_charges", sa.Column("method", sa.String(16), nullable=True))
    op.add_column("patient_charges", sa.Column("payment_reference", sa.String(64), nullable=True))

    # Before the constraint, or every existing row fails it.
    op.execute(
        f"UPDATE patient_charges SET method = 'card' "  # noqa: S608 — literals above
        f"WHERE kind IN ({_sql_list(_COLLECTING_KINDS)})"
    )

    op.create_check_constraint(
        "ck_patient_charges_method",
        "patient_charges",
        f"method IS NULL OR method IN ({_sql_list(_METHODS)})",
    )
    op.create_check_constraint(
        "ck_patient_charges_method_kind",
        "patient_charges",
        f"(kind IN ({_sql_list(_COLLECTING_KINDS)})) = (method IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_patient_charges_other_has_reference",
        "patient_charges",
        "method IS DISTINCT FROM 'other' OR payment_reference IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_patient_charges_other_has_reference", "patient_charges")
    op.drop_constraint("ck_patient_charges_method_kind", "patient_charges")
    op.drop_constraint("ck_patient_charges_method", "patient_charges")
    op.drop_column("patient_charges", "payment_reference")
    op.drop_column("patient_charges", "method")
