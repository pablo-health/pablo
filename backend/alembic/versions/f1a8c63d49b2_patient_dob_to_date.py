# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patients.date_of_birth from text to DATE.

``f4c1a9d3b7e2`` moved the compliance/supervision civil dates from
``VARCHAR(10)`` to ``DATE`` but left the one that matters most — a
patient's date of birth — as a string. It's a civil date (no time, no
timezone), exactly what ``DATE`` models, and ``DATE`` adds the DB-level
validation the string column never had: native range/ordering and
rejection of malformed values.

Per-tenant (each ``practice_{id}`` schema). ``NULLIF(date_of_birth, '')``
maps the empty-string sentinel the old string column tolerated to NULL;
a real ``YYYY-MM-DD`` casts cleanly, and a malformed legacy value aborts
the cast (intended — find it pre-launch). The API still speaks ISO date
strings; the repository converts at the row boundary.

Revision ID: f1a8c63d49b2
Revises: e7c4b9a25f18
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["branch_labels", "depends_on", "down_revision", "revision"]

revision: str = "f1a8c63d49b2"
down_revision: str | Sequence[str] | None = "e7c4b9a25f18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "patients",
        "date_of_birth",
        existing_type=sa.String(length=10),
        type_=sa.Date(),
        existing_nullable=True,
        postgresql_using="NULLIF(date_of_birth, '')::date",
    )


def downgrade() -> None:
    op.alter_column(
        "patients",
        "date_of_birth",
        existing_type=sa.Date(),
        type_=sa.String(length=10),
        existing_nullable=True,
        postgresql_using="date_of_birth::text",
    )
