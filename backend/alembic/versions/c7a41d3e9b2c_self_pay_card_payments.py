# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""self-pay card payments: card on file, the charge ledger, and event dedupe

Three tables.

Two live in the practice schema. ``patient_payment_methods`` is the card a
practice keeps on file for a client and holds processor ids plus the display
triple (brand, last four, expiry) — there is deliberately no column a card
number or CVC could be written into, because the browser posts the card
straight to the processor and this database only ever learns an opaque
payment-method id. ``patient_charges`` is the ledger: one row per charge
attempt, written ``pending`` before the processor is called so an attempt that
dies mid-flight still leaves a record, then updated from the outcome.

Both carry ``patient_id``, so ``enable_rls_on_schema`` force-enables row-level
security and attaches the standard ``has_patient_access`` policy from its
generic ``patient_id`` branch. That is the wanted posture: these rows are
financial records about one client, and only a clinician with a grant on that
client should see them. Neither table is registered not-row-scoped, which would
strip a real per-row guard, and neither is registered patient-readable — the
client-facing side of this does not exist yet, and a grant nobody has a route
for is a grant nobody is watching.

``created_by_user_id`` is deliberately not named ``user_id``. The policy branch
order checks ``user_id`` before ``patient_id``, so that name would silently
swap the patient-access policy for direct ownership, and the clinician who took
the payment would become the only one who could ever see the row.

The third, ``platform.processed_payment_events``, is the webhook dedupe ledger.
It is platform-scoped because most events arriving on a practice's processor
account are not this application's business at all and carry nothing that names
a practice — those still have to be recorded so the processor stops retrying
them, and there is no practice schema to record them in.

Revision ID: c7a41d3e9b2c
Revises: 2f1ec5526619
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c7a41d3e9b2c"
down_revision: str | Sequence[str] | None = "2f1ec5526619"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PLATFORM_SCHEMA = "platform"


def upgrade() -> None:
    op.create_table(
        "patient_payment_methods",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("stripe_customer_id", sa.String(length=255), nullable=False),
        sa.Column("stripe_payment_method_id", sa.String(length=255), nullable=True),
        sa.Column("card_brand", sa.String(length=32), nullable=True),
        sa.Column("card_last4", sa.String(length=4), nullable=True),
        sa.Column("card_exp_month", sa.SmallInteger(), nullable=True),
        sa.Column("card_exp_year", sa.SmallInteger(), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # One card on file per client: re-running setup replaces it in place rather
    # than accumulating stale cards the clinician would have to choose between.
    op.create_index(
        "ux_patient_payment_methods_patient_id",
        "patient_payment_methods",
        ["patient_id"],
        unique=True,
    )

    op.create_table(
        "patient_charges",
        sa.Column("id", sa.String(length=128), primary_key=True),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("appointment_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="usd"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.String(length=255), nullable=True),
        sa.Column("status_detail", sa.String(length=128), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'refunded')",
            name="ck_patient_charges_status",
        ),
        # Money is integer minor units and a charge is for a positive amount; a
        # refund is a status transition on this row, never a negative charge.
        sa.CheckConstraint("amount_cents > 0", name="ck_patient_charges_amount_positive"),
    )
    op.create_index(
        "ix_patient_charges_patient_created",
        "patient_charges",
        ["patient_id", "created_at"],
    )
    # The webhook finds its row by the PaymentIntent id; unique so a replayed
    # event can never fan out across two rows. Partial, because many rows
    # legitimately sit at NULL between the insert and the processor call.
    op.create_index(
        "ux_patient_charges_payment_intent",
        "patient_charges",
        ["stripe_payment_intent_id"],
        unique=True,
        postgresql_where=sa.text("stripe_payment_intent_id IS NOT NULL"),
    )

    # Platform DDL is written idempotently, like every other platform
    # migration here: env.py bootstraps the platform schema straight from the
    # ORM metadata before the chain runs, so a plain CREATE TABLE would collide
    # with the table it has just made.
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {PLATFORM_SCHEMA}.processed_payment_events (
            event_id VARCHAR(255) NOT NULL PRIMARY KEY,
            event_type VARCHAR(64) NOT NULL,
            practice_id VARCHAR(128),
            event_created_at TIMESTAMP WITH TIME ZONE,
            processed_at TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {PLATFORM_SCHEMA}.processed_payment_events")
    op.drop_index("ux_patient_charges_payment_intent", table_name="patient_charges")
    op.drop_index("ix_patient_charges_patient_created", table_name="patient_charges")
    op.drop_table("patient_charges")
    op.drop_index("ux_patient_payment_methods_patient_id", table_name="patient_payment_methods")
    op.drop_table("patient_payment_methods")
