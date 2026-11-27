# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""session_type fits any appointment type name

``appointment_types.name`` allows 100 characters, and a booking copies the
name into ``session_type``, which allowed 30. Widen it to 100 on the three
tables that hold a type name. Widening a varchar is a catalog-only change.

The downgrade fails, rather than truncates, if a longer name is stored.

Revision ID: d3a7c5e91f24
Revises: 8668091951bb
Create Date: 2026-11-27
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d3a7c5e91f24"
down_revision: str | Sequence[str] | None = "8668091951bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, nullable) for every column that holds an appointment type's name.
_COLUMNS = (
    ("therapy_sessions", True),
    ("appointments", False),
    ("waitlist_entries", True),
)


def _resize(old: int, new: int) -> None:
    for table, nullable in _COLUMNS:
        op.alter_column(
            table,
            "session_type",
            existing_type=sa.String(length=old),
            type_=sa.String(length=new),
            existing_nullable=nullable,
        )


def upgrade() -> None:
    _resize(30, 100)


def downgrade() -> None:
    _resize(100, 30)
