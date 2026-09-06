# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""claim submission: the outbox marker, vendor ids and the receipt ledger

Filing a claim is asynchronous: confirming writes ``validated`` and a
worker sends it later, the clearinghouse acknowledges it later still, and
the payer later than that. ``claims`` gains what that needs on the row:

* ``submission_idempotency_key`` / ``submission_pending_at`` — the outbox's
  pending marker, written before the submission call so a crash between
  the call and the commit is reconciled with the same key instead of
  filing the claim twice;
* ``vendor_claim_id`` — the clearinghouse's id for the filing;
* ``payer_claim_number`` — the payer's claim number from its 277CA, which
  a corrected or void claim quotes back;
* ``submission_findings`` — the edit or status codes behind a rejection;
* ``last_receipt_at`` / ``status_checked_at`` — when the last
  acknowledgement arrived and when the status poll last asked.

``claim_events`` is the receipt ledger: one row per hop and per alert,
with the vendor's event id unique (a redelivered webhook cannot move a
claim twice) and the deadline ladder's rung unique per claim and kind (a
restarted watchdog cannot re-raise an alert). Keyed on ``patient_id`` like
``claim_lines`` so the ``has_patient_access`` policy applies.

Revision ID: d7e2f4a6b8c1
Revises: c3d9e5f7a2b8
Create Date: 2026-09-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d7e2f4a6b8c1"
down_revision: str | Sequence[str] | None = "c3d9e5f7a2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLAIM_EVENT_KINDS = (
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


def upgrade() -> None:
    op.add_column("claims", sa.Column("vendor_claim_id", sa.String(length=80), nullable=True))
    op.add_column("claims", sa.Column("payer_claim_number", sa.String(length=80), nullable=True))
    op.add_column(
        "claims", sa.Column("submission_idempotency_key", sa.String(length=120), nullable=True)
    )
    op.add_column(
        "claims", sa.Column("submission_pending_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("claims", sa.Column("submission_findings", JSONB(), nullable=True))
    op.add_column("claims", sa.Column("last_receipt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "claims", sa.Column("status_checked_at", sa.DateTime(timezone=True), nullable=True)
    )

    kinds = ", ".join(f"'{kind}'" for kind in _CLAIM_EVENT_KINDS)
    op.create_table(
        "claim_events",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("claim_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("from_state", sa.String(length=16), nullable=True),
        sa.Column("to_state", sa.String(length=16), nullable=True),
        sa.Column("deadline_kind", sa.String(length=16), nullable=True),
        sa.Column("rung", sa.Integer(), nullable=True),
        sa.Column("vendor_event_id", sa.String(length=128), nullable=True),
        sa.Column("vendor_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("detail", JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="claim_events_pkey"),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.id"], name="fk_claim_events_claim_id_claims", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            name="fk_claim_events_patient_id_patients",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"kind IN ({kinds})", name="ck_claim_events_kind"),
        sa.UniqueConstraint("vendor_event_id", name="ux_claim_events_vendor_event_id"),
        sa.UniqueConstraint(
            "claim_id", "kind", "deadline_kind", "rung", name="ux_claim_events_deadline_rung"
        ),
    )
    op.create_index("ix_claim_events_claim_id", "claim_events", ["claim_id"])
    op.create_index("ix_claim_events_patient_id", "claim_events", ["patient_id"])
    op.create_index(
        "ix_claim_events_vendor_transaction_id", "claim_events", ["vendor_transaction_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_claim_events_vendor_transaction_id", table_name="claim_events")
    op.drop_index("ix_claim_events_patient_id", table_name="claim_events")
    op.drop_index("ix_claim_events_claim_id", table_name="claim_events")
    op.drop_table("claim_events")
    for column in (
        "status_checked_at",
        "last_receipt_at",
        "submission_findings",
        "submission_pending_at",
        "submission_idempotency_key",
        "payer_claim_number",
        "vendor_claim_id",
    ):
        op.drop_column("claims", column)
