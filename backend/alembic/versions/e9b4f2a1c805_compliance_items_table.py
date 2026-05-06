# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""compliance_items table

Adds the per-therapist compliance reminder table (license renewal,
malpractice insurance, CAQH re-attestation, HIPAA training, NPI). These
are the clinician's own credentials — not PHI — so the table lives in the
practice schema with the rest of the user-scoped data but does not feed
the audit log.

Revision ID: e9b4f2a1c805
Revises: d7a3f1c8e2b4
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "e9b4f2a1c805"
down_revision: str | Sequence[str] | None = "d7a3f1c8e2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "compliance_items",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("item_type", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("due_date", sa.String(length=10), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_items_user_id",
        "compliance_items",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_items_user_id", table_name="compliance_items")
    op.drop_table("compliance_items")
