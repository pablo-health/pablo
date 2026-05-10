# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-context chat primitive — domain dataclasses and Pydantic models.

The chat primitive is prompt-agnostic. Callers (chart Q&A, prescription
justification, …) supply the system prompt and the source-selection
default; the primitive itself ships no clinical opinion.

PHI lives in:
* ``ChatMessage.content`` (free text from the user or the model)
* ``ChatConversation.caller_system_prompt`` (PHI-adjacent — instructions
  that may name the chart shape)
* ``ChatMessage.source_selection.pasted_text`` (user-pasted prose)

PHI never lives in:
* ``ChatMessage.context_manifest`` — counts and ids only
* Audit-log payloads — model id, token counts, manifest digest
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Domain dataclasses (storage shape)
# --------------------------------------------------------------------------- #


@dataclass
class ChatConversation:
    id: str
    patient_id: str
    owner_user_id: str
    title: str
    caller_system_prompt: str
    caller_feature_key: str
    default_source_selection: dict[str, Any]
    created_at: datetime
    last_turn_at: datetime | None = None
    archived_at: datetime | None = None


@dataclass
class ChatMessage:
    id: str
    conversation_id: str
    sequence: int
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    source_selection: dict[str, Any] | None = None
    context_manifest: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    llm_model: str | None = None
    llm_finish_reason: str | None = None
    llm_error: str | None = None


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #


class CreateChatConversationRequest(BaseModel):
    patient_id: str
    caller_feature_key: str = Field(min_length=1, max_length=100)
    caller_system_prompt: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    default_source_selection: dict[str, Any] | None = None


class UpdateChatConversationRequest(BaseModel):
    """Mutable fields only.

    ``patient_id``, ``caller_feature_key``, ``caller_system_prompt``,
    and ``owner_user_id`` are deliberately immutable — a caller that
    needs a different prompt creates a new conversation. This preserves
    the audit guarantee that every turn in one conversation was
    generated under one declared system prompt.
    """

    title: str | None = Field(default=None, max_length=200)
    default_source_selection: dict[str, Any] | None = None
    archive: bool | None = None


class SendChatMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=32_768)
    source_selection: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# Response models
# --------------------------------------------------------------------------- #


class ChatConversationResponse(BaseModel):
    id: str
    patient_id: str
    owner_user_id: str
    title: str
    caller_feature_key: str
    default_source_selection: dict[str, Any]
    created_at: datetime
    last_turn_at: datetime | None = None
    archived_at: datetime | None = None

    @staticmethod
    def from_conversation(conv: ChatConversation) -> ChatConversationResponse:
        return ChatConversationResponse(
            id=conv.id,
            patient_id=conv.patient_id,
            owner_user_id=conv.owner_user_id,
            title=conv.title,
            caller_feature_key=conv.caller_feature_key,
            default_source_selection=conv.default_source_selection,
            created_at=conv.created_at,
            last_turn_at=conv.last_turn_at,
            archived_at=conv.archived_at,
        )


class ChatMessageResponse(BaseModel):
    id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    created_at: datetime
    context_manifest: dict[str, Any] | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    llm_model: str | None = None
    llm_finish_reason: str | None = None
    llm_error: str | None = None

    @staticmethod
    def from_message(msg: ChatMessage) -> ChatMessageResponse:
        return ChatMessageResponse(
            id=msg.id,
            conversation_id=msg.conversation_id,
            sequence=msg.sequence,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
            context_manifest=msg.context_manifest,
            input_tokens=msg.input_tokens,
            output_tokens=msg.output_tokens,
            llm_model=msg.llm_model,
            llm_finish_reason=msg.llm_finish_reason,
            llm_error=msg.llm_error,
        )


class ChatConversationDetail(ChatConversationResponse):
    messages: list[ChatMessageResponse] = Field(default_factory=list)


class ChatConversationListResponse(BaseModel):
    data: list[ChatConversationResponse]
    total: int
