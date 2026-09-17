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

AND THE BACKFILL MUST BE ABLE TO SEE THE ROWS IT IS CORRECTING. Per-tenant
tables are provisioned ``FORCE ROW LEVEL SECURITY`` and the migration runs as a
role without BYPASSRLS, while ``env.py`` sets only ``search_path`` — so the GUC
the policies key on is unset and ``has_patient_access`` matches nothing. An
``UPDATE`` in that state touches zero rows and reports no error, whereas
``ADD CONSTRAINT`` validates the table with a scan that is NOT policy-filtered.
The backfill would silently skip every row and the constraint would then reject
those same rows: a tenant holding a single session charge fails, and only a
tenant holding none appears to succeed.

So the work is bracketed by the snapshot/suspend/restore pair that
``e7c4b9a25f18`` introduced for the same reason. The state is restored from
what was actually observed rather than switched on unconditionally — a
deployment that does not force RLS on this table must not acquire it from a
payments migration.

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


def _suspend_rls() -> None:
    """Record ``patient_charges``' RLS flags, then stand them down.

    Only rows where ``relrowsecurity`` is already true are captured, so a table
    that never had RLS produces an empty snapshot and ``_restore_rls`` leaves it
    exactly as it found it.

    The temp table is named for this revision rather than shared, so a suspend
    in another migration can't collide with this one. It is spelled literally
    in the SQL below — interpolating even a module constant into a statement
    reads as an injection site to the linter, and there is nothing here that
    needs to vary.
    """
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            DROP TABLE IF EXISTS _charge_method_rls_state;
            CREATE TEMP TABLE _charge_method_rls_state AS
            SELECT n.nspname AS schema_name, c.relname AS table_name,
                   c.relrowsecurity AS rls_enabled,
                   c.relforcerowsecurity AS rls_forced
            FROM pg_class c
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = current_schema()
              AND c.relname = 'patient_charges'
              AND c.relrowsecurity;

            FOR r IN SELECT * FROM _charge_method_rls_state LOOP
                EXECUTE format('ALTER TABLE %I.%I NO FORCE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
                EXECUTE format('ALTER TABLE %I.%I DISABLE ROW LEVEL SECURITY',
                               r.schema_name, r.table_name);
            END LOOP;
        END $$;"""
    )


def _restore_rls() -> None:
    """Put back exactly the flags ``_suspend_rls`` observed."""
    op.execute(
        """DO $$
        DECLARE r RECORD;
        BEGIN
            IF to_regclass('_charge_method_rls_state') IS NULL THEN
                RETURN;
            END IF;
            FOR r IN SELECT * FROM _charge_method_rls_state LOOP
                IF r.rls_enabled THEN
                    EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
                IF r.rls_forced THEN
                    EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',
                                   r.schema_name, r.table_name);
                END IF;
            END LOOP;
            DROP TABLE IF EXISTS _charge_method_rls_state;
        END $$;"""
    )


def upgrade() -> None:
    op.add_column("patient_charges", sa.Column("method", sa.String(16), nullable=True))
    op.add_column("patient_charges", sa.Column("payment_reference", sa.String(64), nullable=True))

    # The UPDATE below and the constraint validation after it must agree about
    # which rows exist; under FORCE RLS as a NOBYPASSRLS role they do not.
    _suspend_rls()
    try:
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
    finally:
        # A failure here aborts the transaction and rolls the flags back with
        # everything else; restoring in `finally` is for the case where the
        # migration is driven with the DDL already committed.
        _restore_rls()


def downgrade() -> None:
    op.drop_constraint("ck_patient_charges_other_has_reference", "patient_charges")
    op.drop_constraint("ck_patient_charges_method_kind", "patient_charges")
    op.drop_constraint("ck_patient_charges_method", "patient_charges")
    op.drop_column("patient_charges", "payment_reference")
    op.drop_column("patient_charges", "method")
