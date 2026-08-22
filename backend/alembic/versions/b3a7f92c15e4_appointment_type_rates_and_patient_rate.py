# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""appointment_types table + patient rate override columns

Two independent places a session fee can come from, resolved in a fixed
order (patient override -> appointment-type default -> unset). See
``app.scheduling_engine.services.rate_resolver``.

* ``appointment_types`` — practice-level default fee per named appointment
  type (e.g. "individual", "intake"), edited in practice settings.
  ``default_fee_cents`` NULL means no default set yet, not free.
* ``patients.rate_cents`` — per-patient rate override that wins over the
  type default when set. Reduced-fee and sliding-scale arrangements are
  per-person, so this is a real column rather than a note.
* ``patients.sliding_scale_note`` — free text recording the arrangement in
  the clinician's own words. Never parsed or used in arithmetic.

Both new patient columns are NULL-default so existing patients keep
working unchanged. Fees are integer minor units (cents) throughout — no
float arithmetic in the rate path.

Revision ID: b3a7f92c15e4
Revises: 931e7eda0911
Create Date: 2026-08-15
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "b3a7f92c15e4"
down_revision: str | Sequence[str] | None = "931e7eda0911"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "appointment_types",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("default_fee_cents", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_appointment_types_user_name"),
    )
    op.create_index(
        op.f("ix_appointment_types_user_id"),
        "appointment_types",
        ["user_id"],
        unique=False,
    )
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS rate_cents INTEGER NULL")
    op.execute("ALTER TABLE patients ADD COLUMN IF NOT EXISTS sliding_scale_note TEXT NULL")


def downgrade() -> None:
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS sliding_scale_note")
    op.execute("ALTER TABLE patients DROP COLUMN IF EXISTS rate_cents")
    op.drop_index(op.f("ix_appointment_types_user_id"), table_name="appointment_types")
    op.drop_table("appointment_types")
