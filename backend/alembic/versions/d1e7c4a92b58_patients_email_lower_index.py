# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""patients lower(email) index

Backs ``PatientRepository.find_by_email`` (public booking's reuse-an-
existing-chart lookup), whose ``lower(email) = ?`` predicate cannot use
a plain column index and would otherwise sequential-scan ``patients``
on every booking POST.

Partial on ``deleted_at IS NULL`` to match the only query shape and to
keep soft-deleted charts out of the index.

Revision ID: d1e7c4a92b58
Revises: c8d2f4a71e93
Create Date: 2026-08-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d1e7c4a92b58"
down_revision: str | Sequence[str] | None = "c8d2f4a71e93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_patients_email_lower",
        "patients",
        [sa.text("lower(email)")],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_patients_email_lower", table_name="patients")
