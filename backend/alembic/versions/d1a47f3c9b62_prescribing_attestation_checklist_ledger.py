"""prescribing attestation checklist ledger

Adds the verification ledger behind "no checkbox without evidence": a
per-tenant ``prescribing_checklist_items`` table holding one row per
applicable rule item on a prescribing encounter — its computed status, flag
behavior, requirement level, and (once bound) the evidence link that
satisfies it. The attestation service
(``app.prescribing.attestation``) upserts these rows from the enforcement
evaluator's output; uniqueness is per ``(encounter_id, item_id)`` so a
re-evaluation upserts rather than duplicates.

Per-tenant (each ``practice_{id}`` schema); access is the application-layer
``has_patient_access`` RLS policy via ``patient_id``, auto-applied to any
tenant table carrying that column — same as the encounter and prescriptions.

Revision ID: d1a47f3c9b62
Revises: c4688e2f7a27
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "d1a47f3c9b62"
down_revision: str | Sequence[str] | None = "c4688e2f7a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirror the rules-engine enforcement vocabularies (app.rules.enforcement);
# kept as literals here so the migration is self-contained and doesn't import
# application code.
_STATUSES = ("satisfied", "missing", "na")
_FLAG_BEHAVIORS = ("hard_stop", "soft_warn", "info")
_REQUIREMENT_LEVELS = ("required", "conditional", "recommended")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    op.create_table(
        "prescribing_checklist_items",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("item_id", sa.String(length=120), nullable=False),
        sa.Column("requirement_level", sa.String(length=20), nullable=False),
        sa.Column("flag_behavior", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("evidence_link", sa.String(length=512), nullable=True),
        sa.Column("captured_by", sa.String(length=128), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authority_ref", sa.String(length=255), nullable=True),
        sa.Column("ruleset_version", sa.String(length=40), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint(
            "encounter_id",
            "item_id",
            name="uq_prescribing_checklist_items_encounter_item",
        ),
        sa.CheckConstraint(
            f"status IN ({_in_list(_STATUSES)})",
            name="ck_prescribing_checklist_items_status",
        ),
        sa.CheckConstraint(
            f"flag_behavior IN ({_in_list(_FLAG_BEHAVIORS)})",
            name="ck_prescribing_checklist_items_flag_behavior",
        ),
        sa.CheckConstraint(
            f"requirement_level IN ({_in_list(_REQUIREMENT_LEVELS)})",
            name="ck_prescribing_checklist_items_requirement_level",
        ),
    )
    op.create_index(
        op.f("ix_prescribing_checklist_items_encounter_id"),
        "prescribing_checklist_items",
        ["encounter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prescribing_checklist_items_patient_id"),
        "prescribing_checklist_items",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_prescribing_checklist_items_patient_id"),
        table_name="prescribing_checklist_items",
    )
    op.drop_index(
        op.f("ix_prescribing_checklist_items_encounter_id"),
        table_name="prescribing_checklist_items",
    )
    op.drop_table("prescribing_checklist_items")
