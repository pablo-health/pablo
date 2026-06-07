"""create prescribing encounters and prescriptions tables

Adds the per-tenant ``prescribing_encounters`` and ``prescriptions`` tables —
the encounter + prescription record the prescribing rules engine evaluates,
siblings of ``notes`` / ``diagnostic_assessments`` inside each
``practice_{id}`` schema. Access is enforced at the application layer via
``has_patient_access`` (keyed on ``patient_id``), same as the rest of the
per-patient chart — no separate RLS policy.

One encounter has zero or more prescriptions (``prescriptions.encounter_id``
-> ``prescribing_encounters.id``). The engine evaluates each prescription
(``schedule`` + ``drug_class`` + the quantitative fields) against the
encounter context (state, modality, prior in-person, ...). The encounter is
stamped with the ruleset version in force.

Revision ID: 28220bd1eec6
Revises: b3c765d511ee
Create Date: 2026-06-07
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "28220bd1eec6"
down_revision: str | Sequence[str] | None = "b3c765d511ee"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prescribing_encounters",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("prescriber_user_id", sa.String(length=128), nullable=False),
        sa.Column("prescriber_type", sa.String(length=40), nullable=True),
        sa.Column("prescriber_npi", sa.String(length=20), nullable=True),
        sa.Column("prescriber_dea", sa.String(length=50), nullable=True),
        sa.Column("prescriber_license", sa.String(length=100), nullable=True),
        sa.Column("delegation_ref", sa.String(length=128), nullable=True),
        sa.Column("delegating_physician_name", sa.String(length=255), nullable=True),
        sa.Column("delegating_physician_dea", sa.String(length=50), nullable=True),
        sa.Column("state", sa.String(length=2), nullable=True),
        sa.Column("modality", sa.String(length=20), nullable=True),
        sa.Column("prior_in_person", sa.Boolean(), nullable=True),
        sa.Column("patient_in_sud_program", sa.Boolean(), nullable=True),
        sa.Column("ruleset_version", sa.String(length=40), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("encountered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('open', 'finalized', 'voided')",
            name="ck_prescribing_encounters_status",
        ),
        sa.CheckConstraint(
            "modality IS NULL OR modality IN ('in_person', 'audio_video', 'audio_only', 'async')",
            name="ck_prescribing_encounters_modality",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_prescribing_encounters_patient_id"),
        "prescribing_encounters",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prescribing_encounters_prescriber_user_id"),
        "prescribing_encounters",
        ["prescriber_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_prescribing_encounters_patient_encountered",
        "prescribing_encounters",
        ["patient_id", "encountered_at"],
        unique=False,
    )

    op.create_table(
        "prescriptions",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("encounter_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("patient_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("rxnorm_id", sa.String(length=20), nullable=True),
        sa.Column("drug_name", sa.String(length=200), nullable=True),
        sa.Column("schedule", sa.String(length=4), nullable=False),
        sa.Column("drug_class", sa.String(length=20), nullable=False),
        sa.Column("strength", sa.String(length=100), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("days_supply", sa.Integer(), nullable=True),
        sa.Column("refills", sa.Integer(), nullable=False),
        sa.Column("indication", sa.String(length=40), nullable=True),
        sa.Column("first_in_course", sa.Boolean(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "schedule IN ('II', 'III', 'IV', 'V', 'none')",
            name="ck_prescriptions_schedule",
        ),
        sa.CheckConstraint(
            "drug_class IN ('opioid', 'stimulant', 'benzodiazepine', 'buprenorphine', 'other')",
            name="ck_prescriptions_drug_class",
        ),
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
        op.f("ix_prescriptions_encounter_id"),
        "prescriptions",
        ["encounter_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_prescriptions_patient_id"),
        "prescriptions",
        ["patient_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_prescriptions_patient_id"), table_name="prescriptions")
    op.drop_index(op.f("ix_prescriptions_encounter_id"), table_name="prescriptions")
    op.drop_table("prescriptions")
    op.drop_index(
        "ix_prescribing_encounters_patient_encountered",
        table_name="prescribing_encounters",
    )
    op.drop_index(
        op.f("ix_prescribing_encounters_prescriber_user_id"),
        table_name="prescribing_encounters",
    )
    op.drop_index(
        op.f("ix_prescribing_encounters_patient_id"),
        table_name="prescribing_encounters",
    )
    op.drop_table("prescribing_encounters")
