# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""llm_usage aggregate table

Adds the per-tenant monthly LLM usage roll-up backing
``LlmUsageMeter`` (THERAPY-f6eg, Phase 3b of THERAPY-bhv). See design
doc §11.6.

The table lives in the practice schema next to ``chat_conversations``;
schema-per-practice already isolates tenants so no ``tenant_id``
column. Per-turn forensic detail (content, manifest, finish_reason)
stays on ``chat_messages`` — this table only carries summable counts.

Aggregation key is ``(user_id, feature_key, period_yyyymm, model)``.
Per design doc §11.6, monthly buckets are sufficient — quota windows
are billing-cycle granularity, not real-time. ``record_turn`` upserts;
``get_period_usage`` reads.

Revision ID: a5f1c8d9e472
Revises: c4e9a7b3f180
Create Date: 2026-05-13
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "a5f1c8d9e472"
down_revision: str | Sequence[str] | None = "c4e9a7b3f180"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("feature_key", sa.String(length=64), nullable=False),
        sa.Column("period_yyyymm", sa.String(length=6), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("turn_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("first_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint(
            "user_id",
            "feature_key",
            "period_yyyymm",
            "model",
            name="pk_llm_usage",
        ),
        sa.CheckConstraint(
            "period_yyyymm ~ '^[0-9]{6}$'",
            name="ck_llm_usage_period_yyyymm_shape",
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND turn_count >= 0",
            name="ck_llm_usage_nonneg",
        ),
    )
    op.create_index(
        "ix_llm_usage_period",
        "llm_usage",
        ["period_yyyymm"],
    )
    op.create_index(
        "ix_llm_usage_feature_period",
        "llm_usage",
        ["feature_key", "period_yyyymm"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_usage_feature_period", table_name="llm_usage")
    op.drop_index("ix_llm_usage_period", table_name="llm_usage")
    op.drop_table("llm_usage")
