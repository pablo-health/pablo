"""civil-date columns from text to DATE

The compliance/supervision tables stored user-entered calendar dates as
``VARCHAR(10)`` ISO strings. They are civil dates (no time, no timezone), which
is exactly what ``DATE`` models — and ``DATE`` adds DB-level validation, native
range/ordering, and rejects malformed values that the string column silently
accepted. Migrate them in place; existing ISO strings cast cleanly.

Columns (all per-tenant, each ``practice_{id}`` schema):
* ``compliance_items.due_date``
* ``supervision_relationships.effective_date`` / ``next_review_date``
* ``supervision_hours.logged_date``

Revision ID: f4c1a9d3b7e2
Revises: e2b9d4a1c6f3
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f4c1a9d3b7e2"
down_revision: str | Sequence[str] | None = "e2b9d4a1c6f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "compliance_items",
        "due_date",
        existing_type=sa.String(length=10),
        type_=sa.Date(),
        existing_nullable=True,
        postgresql_using="due_date::date",
    )
    op.alter_column(
        "supervision_relationships",
        "effective_date",
        existing_type=sa.String(length=10),
        type_=sa.Date(),
        existing_nullable=True,
        postgresql_using="effective_date::date",
    )
    op.alter_column(
        "supervision_relationships",
        "next_review_date",
        existing_type=sa.String(length=10),
        type_=sa.Date(),
        existing_nullable=True,
        postgresql_using="next_review_date::date",
    )
    op.alter_column(
        "supervision_hours",
        "logged_date",
        existing_type=sa.String(length=10),
        type_=sa.Date(),
        existing_nullable=False,
        postgresql_using="logged_date::date",
    )


def downgrade() -> None:
    # ``date::text`` renders ISO ``YYYY-MM-DD`` — the exact prior storage format.
    op.alter_column(
        "supervision_hours",
        "logged_date",
        existing_type=sa.Date(),
        type_=sa.String(length=10),
        existing_nullable=False,
        postgresql_using="logged_date::text",
    )
    op.alter_column(
        "supervision_relationships",
        "next_review_date",
        existing_type=sa.Date(),
        type_=sa.String(length=10),
        existing_nullable=True,
        postgresql_using="next_review_date::text",
    )
    op.alter_column(
        "supervision_relationships",
        "effective_date",
        existing_type=sa.Date(),
        type_=sa.String(length=10),
        existing_nullable=True,
        postgresql_using="effective_date::text",
    )
    op.alter_column(
        "compliance_items",
        "due_date",
        existing_type=sa.Date(),
        type_=sa.String(length=10),
        existing_nullable=True,
        postgresql_using="due_date::text",
    )
