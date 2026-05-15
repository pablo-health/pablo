# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pydantic request/response models for the patient-context chat API.

See ``docs/architecture/patient-context-chat-oss.md`` §6 for the
endpoint contracts. Phase 1 covers the conversation-lifecycle surface
(create / get / list / patch / delete); the streaming-message endpoint
arrives in Phase 3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .chat import ChatConversation, ChatMessage  # noqa: TC001 — Pydantic runtime


class CreateChatConversationRequest(BaseModel):
    """``POST /api/chat/conversations`` request body."""

    patient_id: str
    caller_feature_key: str = Field(min_length=1, max_length=64)
    caller_system_prompt: str = Field(min_length=1, max_length=16_384)
    title: str | None = Field(default=None, max_length=200)
    default_source_selection: dict[str, Any] | None = None


class UpdateChatConversationRequest(BaseModel):
    """``PATCH /api/chat/conversations/{id}`` request body.

    Immutable fields (``patient_id``, ``caller_system_prompt``,
    ``caller_feature_key``, ``owner_user_id``) are intentionally not
    accepted here. To change the system prompt, create a new
    conversation.
    """

    title: str | None = Field(default=None, max_length=200)
    default_source_selection: dict[str, Any] | None = None
    archive: bool | None = None


class ChatMessageResponse(BaseModel):
    """API shape for a single turn."""

    id: str
    conversation_id: str
    sequence: int
    role: str
    content: str
    created_at: datetime
    source_selection: dict[str, Any] | None = None
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
            source_selection=msg.source_selection,
            context_manifest=msg.context_manifest,
            input_tokens=msg.input_tokens,
            output_tokens=msg.output_tokens,
            llm_model=msg.llm_model,
            llm_finish_reason=msg.llm_finish_reason,
            llm_error=msg.llm_error,
        )


class ChatConversationResponse(BaseModel):
    """API shape for a conversation envelope. Messages omitted (list view)."""

    id: str
    patient_id: str
    owner_user_id: str
    title: str
    caller_feature_key: str
    default_source_selection: dict[str, Any] | None = None
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


class ChatConversationDetailResponse(ChatConversationResponse):
    """Conversation envelope plus its messages (detail view)."""

    messages: list[ChatMessageResponse] = Field(default_factory=list)

    @staticmethod
    def from_conversation_with_messages(
        conv: ChatConversation, messages: list[ChatMessage]
    ) -> ChatConversationDetailResponse:
        return ChatConversationDetailResponse(
            id=conv.id,
            patient_id=conv.patient_id,
            owner_user_id=conv.owner_user_id,
            title=conv.title,
            caller_feature_key=conv.caller_feature_key,
            default_source_selection=conv.default_source_selection,
            created_at=conv.created_at,
            last_turn_at=conv.last_turn_at,
            archived_at=conv.archived_at,
            messages=[ChatMessageResponse.from_message(m) for m in messages],
        )


class ChatConversationListResponse(BaseModel):
    """``GET /api/chat/conversations`` response."""

    data: list[ChatConversationResponse]
    total: int


class PreviewChatContextRequest(BaseModel):
    """``POST /api/chat/conversations/preview`` request body.

    Drives the §13.4 briefing card. Runs the same context bundler the
    streaming turn would, against the caller's proposed
    ``source_selection``, and returns the resulting manifest — without
    creating a conversation, calling the LLM, or recording any audit
    rows. PHI never leaves the practice schema; the manifest is
    counts / ids / dates only.

    Omitting ``source_selection`` falls back to the design-doc §7.4
    default (medications + intake + recent progress notes + plans).
    """

    patient_id: str
    source_selection: dict[str, Any] | None = None


class PreviewChatContextResponse(BaseModel):
    """``POST /api/chat/conversations/preview`` response body."""

    manifest: dict[str, Any]


class SendChatMessageRequest(BaseModel):
    """``POST /api/chat/conversations/{id}/messages`` request body.

    The streaming-message endpoint per design doc §6.4. ``source_selection``
    is an optional per-message override; when omitted the conversation's
    ``default_source_selection`` is used. ``model`` is an optional
    per-conversation model override used by downstream consumers that
    pin specific features to a Pro-tier model — default callers leave
    it unset and the resolver picks ``settings.ai_model_flash``.
    """

    content: str = Field(min_length=1, max_length=32_768)
    source_selection: dict[str, Any] | None = None
    model: str | None = Field(default=None, max_length=128)
