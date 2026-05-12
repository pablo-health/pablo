# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-context chat domain dataclasses (THERAPY-bhv).

A ChatConversation is a clinician working-memory artifact bound to one
patient at creation. ChatMessages are append-only turns inside it.
See ``docs/architecture/patient-context-chat-oss.md`` for the full
design.

Tenant isolation is by PostgreSQL schema (schema-per-practice), so
neither dataclass carries a ``tenant_id`` field — the schema *is* the
tenant. This matches Patient / Note / TherapySession.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class ChatConversation:
    """A patient-context chat conversation envelope.

    ``patient_id`` and ``caller_system_prompt`` are immutable after
    insert — a clinician who wants a different system prompt creates a
    new conversation. This preserves the audit guarantee that every
    turn in a conversation was generated under one declared prompt.
    """

    id: str
    patient_id: str
    owner_user_id: str
    title: str
    caller_system_prompt: str
    caller_feature_key: str
    created_at: datetime
    default_source_selection: dict[str, Any] | None = None
    last_turn_at: datetime | None = None
    archived_at: datetime | None = None


@dataclass
class ChatMessage:
    """A single turn (user or assistant) inside a ChatConversation.

    ``sequence`` is monotonic per conversation starting at 1; the
    ``(conversation_id, sequence)`` pair is unique. Assistant turns
    carry token counts, model id, and finish reason; user turns carry
    the active source-selection snapshot and the assembler manifest.
    """

    id: str
    conversation_id: str
    sequence: int
    role: str  # "user" | "assistant"
    content: str
    created_at: datetime
    source_selection: dict[str, Any] | None = None
    context_manifest: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    llm_model: str | None = None
    llm_finish_reason: str | None = None
    llm_error: str | None = None
