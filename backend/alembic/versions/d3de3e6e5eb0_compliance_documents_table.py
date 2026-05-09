# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""compliance_documents table

Adds the dormant data-model rail for the Phase 3 compliance vault.
Files attached to a compliance item (license PDFs, malpractice
declarations, CAQH attestations, BAAs) will eventually live here. The
table ships in OSS now — with no routes, storage backend wiring, or UI
— so self-hosters won't be forced to run a schema migration when the
vault product surface lands. ``storage_uri`` is opaque so the backing
storage (Cloud Storage, S3, local fs) can swap without a column change.

Revision ID: d3de3e6e5eb0
Revises: e9b4f2a1c805
Create Date: 2026-05-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3de3e6e5eb0"
down_revision: str | Sequence[str] | None = "e9b4f2a1c805"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "compliance_documents",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("compliance_item_id", sa.String(length=128), nullable=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uploaded_by_user_id", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["compliance_item_id"],
            ["compliance_items.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_compliance_documents_compliance_item_id",
        "compliance_documents",
        ["compliance_item_id"],
        unique=False,
    )
    op.create_index(
        "ix_compliance_documents_uploaded_by_user_id",
        "compliance_documents",
        ["uploaded_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_compliance_documents_uploaded_by_user_id",
        table_name="compliance_documents",
    )
    op.drop_index(
        "ix_compliance_documents_compliance_item_id",
        table_name="compliance_documents",
    )
    op.drop_table("compliance_documents")
