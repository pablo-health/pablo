# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat-conversation and chat-message repositories.

Chat is append-only: ``add_message`` allocates the next ``sequence``
within a conversation and never mutates an existing row except to
finalize the in-progress assistant turn. Conversations themselves are
mutable for ``title``, ``default_source_selection``, and
``archived_at``; everything else is immutable per design.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..models.chat import ChatConversation, ChatMessage


class ChatRepository(ABC):
    @abstractmethod
    def get_conversation(self, conversation_id: str) -> ChatConversation | None: ...

    @abstractmethod
    def add_conversation(self, conv: ChatConversation) -> ChatConversation: ...

    @abstractmethod
    def update_conversation(  # noqa: PLR0913 — keyword-only update verb mirrors the row shape
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        default_source_selection: dict[str, Any] | None = None,
        archived_at: datetime | None = None,
        clear_archived: bool = False,
        last_turn_at: datetime | None = None,
    ) -> ChatConversation | None: ...

    @abstractmethod
    def delete_conversation(self, conversation_id: str) -> None: ...

    @abstractmethod
    def list_conversations(  # noqa: PLR0913 — query verb with optional filters
        self,
        *,
        patient_id: str,
        owner_user_id: str | None = None,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatConversation], int]: ...

    @abstractmethod
    def list_messages(self, conversation_id: str) -> list[ChatMessage]: ...

    @abstractmethod
    def next_sequence(self, conversation_id: str) -> int: ...

    @abstractmethod
    def add_message(self, msg: ChatMessage) -> ChatMessage: ...

    @abstractmethod
    def finalize_assistant_message(  # noqa: PLR0913 — assistant turn finalize captures all LLM result fields
        self,
        message_id: str,
        *,
        content: str,
        input_tokens: int | None,
        output_tokens: int | None,
        llm_model: str | None,
        llm_finish_reason: str | None,
        llm_error: str | None,
    ) -> ChatMessage | None: ...

    @abstractmethod
    def list_owner_conversations_for_patient(
        self, *, patient_id: str, owner_user_id: str
    ) -> list[ChatConversation]: ...


class InMemoryChatRepository(ChatRepository):
    """In-memory implementation used by the test suite."""

    def __init__(self) -> None:
        self._conversations: dict[str, ChatConversation] = {}
        self._messages: dict[str, list[ChatMessage]] = {}

    def get_conversation(self, conversation_id: str) -> ChatConversation | None:
        return self._conversations.get(conversation_id)

    def add_conversation(self, conv: ChatConversation) -> ChatConversation:
        self._conversations[conv.id] = conv
        self._messages.setdefault(conv.id, [])
        return conv

    def update_conversation(  # noqa: PLR0913 — keyword-only update verb mirrors the row shape
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        default_source_selection: dict[str, Any] | None = None,
        archived_at: datetime | None = None,
        clear_archived: bool = False,
        last_turn_at: datetime | None = None,
    ) -> ChatConversation | None:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        if title is not None:
            conv.title = title
        if default_source_selection is not None:
            conv.default_source_selection = default_source_selection
        if clear_archived:
            conv.archived_at = None
        elif archived_at is not None:
            conv.archived_at = archived_at
        if last_turn_at is not None:
            conv.last_turn_at = last_turn_at
        return conv

    def delete_conversation(self, conversation_id: str) -> None:
        self._conversations.pop(conversation_id, None)
        self._messages.pop(conversation_id, None)

    def list_conversations(  # noqa: PLR0913 — query verb with optional filters
        self,
        *,
        patient_id: str,
        owner_user_id: str | None = None,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatConversation], int]:
        rows = [
            c for c in self._conversations.values()
            if c.patient_id == patient_id
        ]
        if owner_user_id:
            rows = [c for c in rows if c.owner_user_id == owner_user_id]
        if caller_feature_key:
            rows = [c for c in rows if c.caller_feature_key == caller_feature_key]
        if not include_archived:
            rows = [c for c in rows if c.archived_at is None]

        rows.sort(
            key=lambda c: (c.last_turn_at or c.created_at),
            reverse=True,
        )
        total = len(rows)
        return rows[offset : offset + limit], total

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        return sorted(
            self._messages.get(conversation_id, []),
            key=lambda m: m.sequence,
        )

    def next_sequence(self, conversation_id: str) -> int:
        msgs = self._messages.get(conversation_id, [])
        if not msgs:
            return 1
        return max(m.sequence for m in msgs) + 1

    def add_message(self, msg: ChatMessage) -> ChatMessage:
        self._messages.setdefault(msg.conversation_id, []).append(msg)
        return msg

    def finalize_assistant_message(  # noqa: PLR0913 — assistant turn finalize captures all LLM result fields
        self,
        message_id: str,
        *,
        content: str,
        input_tokens: int | None,
        output_tokens: int | None,
        llm_model: str | None,
        llm_finish_reason: str | None,
        llm_error: str | None,
    ) -> ChatMessage | None:
        for msgs in self._messages.values():
            for m in msgs:
                if m.id == message_id:
                    m.content = content
                    m.input_tokens = input_tokens
                    m.output_tokens = output_tokens
                    m.llm_model = llm_model
                    m.llm_finish_reason = llm_finish_reason
                    m.llm_error = llm_error
                    return m
        return None

    def list_owner_conversations_for_patient(
        self, *, patient_id: str, owner_user_id: str
    ) -> list[ChatConversation]:
        return [
            c for c in self._conversations.values()
            if c.patient_id == patient_id and c.owner_user_id == owner_user_id
        ]
