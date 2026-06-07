# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""supervision relationships and hours tables

Adds two per-user, PHI-free tables to the practice schema:

* ``supervision_relationships`` — structured oversight relationships a
  clinician must keep current (physician delegation, NP collaborative,
  PA supervision, pre-licensure clinical supervision). The review
  deadline rides an existing ``compliance_items`` row
  (``compliance_item_id``) so the relationship reuses the established
  reminder machinery; the link is nullable so a relationship can be
  recorded before its review item exists.
* ``supervision_hours`` — an accrued-hour log keyed to a relationship,
  for pre-licensure supervision that tracks direct/indirect hours
  toward a board total.

Both tables carry ``user_id`` and so follow the same user-isolation
RLS policy as the rest of the user-owned practice tables — no separate
policy branch is required.

Revision ID: b3c765d511ee
Revises: c7e1a4f9d2b3
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b3c765d511ee"
down_revision: str | Sequence[str] | None = "c7e1a4f9d2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "supervision_relationships",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("compliance_item_id", sa.Uuid(as_uuid=False), nullable=True),
        sa.Column("relationship_type", sa.String(length=50), nullable=False),
        sa.Column("supervisor_name", sa.String(length=255), nullable=False),
        sa.Column("supervisor_credential", sa.String(length=100), nullable=True),
        sa.Column("supervisor_dea", sa.String(length=50), nullable=True),
        sa.Column("supervisor_license", sa.String(length=100), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("effective_date", sa.String(length=10), nullable=True),
        sa.Column("review_cadence_days", sa.Integer(), nullable=True),
        sa.Column("next_review_date", sa.String(length=10), nullable=True),
        sa.Column("authority_ref", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["compliance_item_id"],
            ["compliance_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_supervision_relationships_user_id",
        "supervision_relationships",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_supervision_relationships_compliance_item_id",
        "supervision_relationships",
        ["compliance_item_id"],
        unique=False,
    )

    op.create_table(
        "supervision_hours",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("supervision_relationship_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("logged_date", sa.String(length=10), nullable=False),
        sa.Column("hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("supervisor", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["supervision_relationship_id"],
            ["supervision_relationships.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_supervision_hours_supervision_relationship_id",
        "supervision_hours",
        ["supervision_relationship_id"],
        unique=False,
    )
    op.create_index(
        "ix_supervision_hours_user_id",
        "supervision_hours",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_supervision_hours_user_id",
        table_name="supervision_hours",
    )
    op.drop_index(
        "ix_supervision_hours_supervision_relationship_id",
        table_name="supervision_hours",
    )
    op.drop_table("supervision_hours")
    op.drop_index(
        "ix_supervision_relationships_compliance_item_id",
        table_name="supervision_relationships",
    )
    op.drop_index(
        "ix_supervision_relationships_user_id",
        table_name="supervision_relationships",
    )
    op.drop_table("supervision_relationships")
