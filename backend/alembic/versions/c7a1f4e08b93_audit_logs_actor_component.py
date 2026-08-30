# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""audit_logs.actor_component

Says which part of the system performed an audited action, for rows whose
``actor_type`` is ``system``. NULL for every human actor kind, where
``user_id`` already names who acted.

``actor_type`` gained two kinds alongside this column: ``system`` for
automated work with no human in the loop, and ``platform_staff`` for an
operator of the deployment reading a practice's data from outside it. Both
were previously recorded as ``clinician``, which made the six-year record
claim a practitioner did things they never did. ``actor_type`` itself needs
no DDL — it is an unconstrained VARCHAR with a ``clinician`` server default,
so existing rows keep exactly the meaning they had.

The column is deliberately unconstrained free text rather than an enum or a
CHECK: a new background job should not need a schema migration before it can
audit itself. Length is capped at the application boundary.

``audit_logs`` is per-tenant, so this runs once per schema through the
tenant fan-out and the tenant template must be regenerated.

Revision ID: c7a1f4e08b93
Revises: 0924e5a542b6
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c7a1f4e08b93"
down_revision: str | Sequence[str] | None = "0924e5a542b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _current_schema() -> str:
    return op.get_bind().execute(text("SELECT current_schema()")).scalar_one()


def upgrade() -> None:
    schema = _current_schema()
    op.execute(
        f"ALTER TABLE {schema}.audit_logs ADD COLUMN IF NOT EXISTS actor_component VARCHAR(64)"
    )


def downgrade() -> None:
    schema = _current_schema()
    op.execute(f"ALTER TABLE {schema}.audit_logs DROP COLUMN IF EXISTS actor_component")
