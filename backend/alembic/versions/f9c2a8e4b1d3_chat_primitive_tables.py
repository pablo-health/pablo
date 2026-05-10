"""patient-context chat primitive tables

Adds the two tables that back the OSS patient-chat primitive:

* ``chat_conversations`` — one row per chat thread; bound to a patient
  and a clinician (owner) at creation; carries the caller-supplied
  system prompt and default source-selection rule.
* ``chat_messages`` — one row per turn (user or assistant), in
  monotonic ``sequence`` order within a conversation. Cascades on
  conversation delete.

Both tables live in the per-practice schema. Tenant isolation comes
from the schema itself — no explicit ``tenant_id`` column, matching
the pattern used by ``patients`` / ``notes`` / ``therapy_sessions``.

The tables are created unconditionally; the ``ENABLE_PATIENT_CHAT``
feature flag gates the API surface, not the schema. Flipping the flag
is a config change, not a deploy.

Revision ID: f9c2a8e4b1d3
Revises: d3de3e6e5eb0
Create Date: 2026-05-10
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f9c2a8e4b1d3"
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
        sa.Column("caller_feature_key", sa.String(length=100), nullable=False),
        sa.Column(
            "default_source_selection",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
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
        "ix_chat_conversations_patient_last_turn",
        "chat_conversations",
        ["patient_id", sa.text("last_turn_at DESC NULLS LAST")],
        unique=False,
    )
    op.create_index(
        "ix_chat_conversations_owner_last_turn",
        "chat_conversations",
        ["owner_user_id", sa.text("last_turn_at DESC NULLS LAST")],
        unique=False,
    )
    op.create_index(
        "ix_chat_conversations_feature_key",
        "chat_conversations",
        ["caller_feature_key"],
        unique=False,
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("conversation_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "source_selection", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "context_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("llm_model", sa.String(length=100), nullable=True),
        sa.Column("llm_finish_reason", sa.String(length=30), nullable=True),
        sa.Column("llm_error", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["chat_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_chat_messages_conversation_sequence"
        ),
        sa.CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_chat_messages_role",
        ),
        sa.CheckConstraint(
            "char_length(content) <= 32768",
            name="ck_chat_messages_content_len",
        ),
    )
    op.create_index(
        "ix_chat_messages_conversation_sequence",
        "chat_messages",
        ["conversation_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_chat_messages_created_at",
        "chat_messages",
        ["created_at"],
        unique=False,
    )

    op.create_table(
        "llm_usage",
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("feature_key", sa.String(length=100), nullable=False),
        sa.Column("period_yyyymm", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "input_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "output_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "user_id",
            "feature_key",
            "period_yyyymm",
            "model",
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_usage")
    op.drop_index(
        "ix_chat_messages_created_at", table_name="chat_messages"
    )
    op.drop_index(
        "ix_chat_messages_conversation_sequence", table_name="chat_messages"
    )
    op.drop_table("chat_messages")
    op.drop_index(
        "ix_chat_conversations_feature_key", table_name="chat_conversations"
    )
    op.drop_index(
        "ix_chat_conversations_owner_last_turn", table_name="chat_conversations"
    )
    op.drop_index(
        "ix_chat_conversations_patient_last_turn", table_name="chat_conversations"
    )
    op.drop_table("chat_conversations")
