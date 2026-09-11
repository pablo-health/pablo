# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""remittance_holds: a client bill the engine refused to write

An 835 states the client's share of a claim twice — once as a claim total
(``CLP05``) and again itemised across the service lines as ``PR``-group
adjustments — and every line and every claim must account for the whole gap
between charged and paid. When two of those statements disagree, the engine
posts what the payer paid and withholds the client's ledger row rather than
bill a real person a figure its own arithmetic cannot corroborate. This is
where the withholding is written down.

Carries ``patient_id`` and no ``user_id``, so ``enable_rls_on_schema``
attaches the standard ``has_patient_access`` policy: the clinician who owns
the claim is the one who meets the hold, and nobody else learns it exists.

``posting_key`` is unique — the same key the receipt ledger already dedupes
adjudications on. A remittance delivered twice is one disagreement, and a
second hold would put it in front of a person twice.

No column holds clinical content: amounts the payer reported, CARC/RARC
pairs (numbers from a public list), a control number, and the states and
timestamps of somebody deciding.

Revision ID: a3e71c920d64
Revises: c4e8b1f7a2d9
Create Date: 2026-09-11
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a3e71c920d64"
down_revision: str | Sequence[str] | None = "c4e8b1f7a2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remittance_holds",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("claim_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("control_number", sa.String(length=30), nullable=False),
        sa.Column("posting_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("stated_cents", sa.Integer(), nullable=False),
        sa.Column("computed_cents", sa.Integer(), nullable=False),
        sa.Column("patient_responsibility_cents", sa.Integer(), nullable=False),
        sa.Column("line_control_number", sa.String(length=30), nullable=True),
        sa.Column(
            "codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("line_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payer_name", sa.String(length=255), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_user_id", sa.String(length=128), nullable=True),
        sa.Column("finding", sa.String(length=24), nullable=True),
        sa.ForeignKeyConstraint(["claim_id"], ["claims.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "state IN ('open', 'acknowledged', 'resolved')",
            name="ck_remittance_holds_state",
        ),
        sa.CheckConstraint(
            "reason IN ('patient_responsibility', 'line_balance', 'claim_balance')",
            name="ck_remittance_holds_reason",
        ),
        sa.CheckConstraint(
            "finding IS NULL OR finding IN "
            "('bill_as_stated', 'waived', 'parse_error', 'payer_inconsistent')",
            name="ck_remittance_holds_finding",
        ),
        sa.CheckConstraint(
            "(state = 'resolved') = (resolved_at IS NOT NULL)",
            name="ck_remittance_holds_resolved_at_state",
        ),
        sa.CheckConstraint(
            "(state = 'resolved') = (finding IS NOT NULL)",
            name="ck_remittance_holds_finding_state",
        ),
        sa.CheckConstraint(
            "(reason = 'line_balance') = (line_control_number IS NOT NULL)",
            name="ck_remittance_holds_line_reason",
        ),
        sa.UniqueConstraint("posting_key", name="ux_remittance_holds_posting_key"),
    )
    # The tick reads "every hold still withholding a row", and resolved ones
    # are the majority in the long run. Partial, so the index stays the size
    # of the work rather than the size of history.
    op.create_index(
        "ix_remittance_holds_open",
        "remittance_holds",
        ["state", "detected_at"],
        postgresql_where=sa.text("state <> 'resolved'"),
    )
    op.create_index("ix_remittance_holds_claim_id", "remittance_holds", ["claim_id"])
    op.create_index("ix_remittance_holds_patient_id", "remittance_holds", ["patient_id"])


def downgrade() -> None:
    op.drop_index("ix_remittance_holds_patient_id", table_name="remittance_holds")
    op.drop_index("ix_remittance_holds_claim_id", table_name="remittance_holds")
    op.drop_index("ix_remittance_holds_open", table_name="remittance_holds")
    op.drop_table("remittance_holds")
