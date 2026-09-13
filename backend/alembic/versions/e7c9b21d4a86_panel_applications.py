# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""panel_applications: where each panel application stands, and whose move it is

One row per application to join a payer's panel. Distinct from
``payers.enrollment_status``, which is about exchanging transactions with a
payer she is already contracted with; this is about whether the payer will
contract with her at all.

Row-scoped by ``user_id`` rather than practice-wide. A payer belongs to the
practice — everyone bills the same insurers — but an application belongs to a
person, and carries which panels rejected her and what she is appealing.

Revision ID: e7c9b21d4a86
Revises: d3e8a1c47b95
Create Date: 2026-09-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e7c9b21d4a86"
down_revision: str | Sequence[str] | None = "d3e8a1c47b95"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = (
    "researching",
    "caqh_ready",
    "submitted",
    "in_review",
    "info_requested",
    "contract_received",
    "effective",
    "closed_panel_appeal",
    "denied",
    "recredentialing",
)

_OWNERS = ("pablo", "therapist")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "panel_applications",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column(
            "payer_id",
            sa.Uuid(as_uuid=False),
            sa.ForeignKey("payers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="researching"),
        sa.Column("action_owner", sa.String(16), nullable=False, server_default="pablo"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("awaiting", sa.Text(), nullable=True),
        sa.Column("reference", sa.String(80), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_in_list(_STATUSES)})", name="ck_panel_applications_status"
        ),
        sa.CheckConstraint(
            f"action_owner IN ({_in_list(_OWNERS)})",
            name="ck_panel_applications_action_owner",
        ),
    )
    op.create_index("ix_panel_applications_payer_id", "panel_applications", ["payer_id"])
    op.create_index("ix_panel_applications_user_id", "panel_applications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_panel_applications_user_id", table_name="panel_applications")
    op.drop_index("ix_panel_applications_payer_id", table_name="panel_applications")
    op.drop_table("panel_applications")
