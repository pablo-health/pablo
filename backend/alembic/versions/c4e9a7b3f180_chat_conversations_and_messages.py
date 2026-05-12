# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""chat_conversations and chat_messages tables

Adds the data-model rail for the patient-context chat primitive
(THERAPY-bhv). See ``docs/architecture/patient-context-chat-oss.md`` §5.

Both tables live in the practice schema next to ``patients`` / ``notes``;
schema-per-practice already isolates tenants so neither table carries a
``tenant_id`` column. The migration runs unconditionally even when
``ENABLE_PATIENT_CHAT=false`` — flipping the flag later is a config
change, not a deploy.

Revision ID: c4e9a7b3f180
Revises: d3de3e6e5eb0
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c4e9a7b3f180"
down_revision: str | Sequence[str] | None = "d3de3e6e5eb0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("patient_id", sa.String(length=128), nullable=False),
        sa.Column("owner_user_id", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("caller_system_prompt", sa.Text(), nullable=False),
        sa.Column("caller_feature_key", sa.String(length=64), nullable=False),
        sa.Column("default_source_selection", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_turn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "char_length(caller_system_prompt) BETWEEN 1 AND 16384",
            name="ck_chat_conversations_system_prompt_len",
        ),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 200",
            name="ck_chat_conversations_title_len",
        ),
    )
    op.create_index(
        "ix_chat_conversations_patient_id",
        "chat_conversations",
        ["patient_id"],
    )
    op.create_index(
        "ix_chat_conversations_owner_user_id",
        "chat_conversations",
        ["owner_user_id"],
    )
    op.create_index(
        "ix_chat_conversations_caller_feature_key",
        "chat_conversations",
        ["caller_feature_key"],
    )
    op.create_index(
        "ix_chat_conversations_patient_last_turn",
        "chat_conversations",
        ["patient_id", "last_turn_at"],
    )
    op.create_index(
        "ix_chat_conversations_owner_last_turn",
        "chat_conversations",
        ["owner_user_id", "last_turn_at"],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_selection", postgresql.JSONB(), nullable=True),
        sa.Column("context_manifest", postgresql.JSONB(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("llm_model", sa.String(length=128), nullable=True),
        sa.Column("llm_finish_reason", sa.String(length=32), nullable=True),
        sa.Column("llm_error", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_chat_messages_role",
        ),
        sa.CheckConstraint(
            "char_length(content) BETWEEN 1 AND 32768",
            name="ck_chat_messages_content_len",
        ),
    )
    op.create_index(
        "ix_chat_messages_conversation_id",
        "chat_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ux_chat_messages_conversation_sequence",
        "chat_messages",
        ["conversation_id", "sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ux_chat_messages_conversation_sequence", table_name="chat_messages")
    op.drop_index("ix_chat_messages_conversation_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_conversations_owner_last_turn", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_patient_last_turn", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_caller_feature_key", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_owner_user_id", table_name="chat_conversations")
    op.drop_index("ix_chat_conversations_patient_id", table_name="chat_conversations")
    op.drop_table("chat_conversations")
