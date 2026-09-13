# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""payer_authorizations: her signature letting Pablo speak to payers for her

One row per signature, never edited. Signing a new version adds a row rather
than replacing the old one, so "what authority did you hold when you rang
Aetna in March" is answerable with the version in force in March.

``full_text`` is the operative column. A row naming a version is worth nothing
once that file is edited; a payer or a licensing board asking what authority we
claimed needs the words she was actually shown.

Revision ID: f4a2c8e91d37
Revises: e7c9b21d4a86
Create Date: 2026-09-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f4a2c8e91d37"
down_revision: str | Sequence[str] | None = "e7c9b21d4a86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payer_authorizations",
        sa.Column("id", sa.Uuid(as_uuid=False), primary_key=True),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("full_text", sa.Text(), nullable=False),
        sa.Column("signed_name", sa.String(200), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payer_authorizations_user_id", "payer_authorizations", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_payer_authorizations_user_id", table_name="payer_authorizations")
    op.drop_table("payer_authorizations")
