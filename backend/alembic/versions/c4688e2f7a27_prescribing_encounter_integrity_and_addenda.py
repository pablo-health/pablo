"""prescribing encounter integrity digest and addenda

Adds the tamper-evidence backing for prescribing encounters:

* an ``integrity_digest`` column on ``prescribing_encounters`` — the content
  digest of the finalized encounter snapshot (the genesis link of the
  addendum hash chain), and
* a per-tenant ``prescribing_encounter_addenda`` table — append-only, dated,
  labelled corrections to a finalized encounter, each chained to the prior
  link by ``prev_digest`` -> ``digest``.

Per-tenant (each ``practice_{id}`` schema); access is the application-layer
``has_patient_access`` check via ``patient_id``, same as the encounter.

Revision ID: c4688e2f7a27
Revises: 28220bd1eec6
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4688e2f7a27"
down_revision: str | Sequence[str] | None = "28220bd1eec6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prescribing_encounters",
        sa.Column("integrity_digest", sa.String(length=64), nullable=True),
    )

    op.create_table(
        "prescribing_encounter_addenda",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column("prev_digest", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["encounter_id"],
            ["prescribing_encounters.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prescribing_encounter_addenda_encounter_id"),
        "prescribing_encounter_addenda",
        ["encounter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prescribing_encounter_addenda_patient_id"),
        "prescribing_encounter_addenda",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_prescribing_encounter_addenda_patient_id"),
        table_name="prescribing_encounter_addenda",
    )
    op.drop_index(
        op.f("ix_prescribing_encounter_addenda_encounter_id"),
        table_name="prescribing_encounter_addenda",
    )
    op.drop_table("prescribing_encounter_addenda")
    op.drop_column("prescribing_encounters", "integrity_digest")
