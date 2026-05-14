# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat conversation + message repository contracts.

Phase 1 (THERAPY-tdh) only exercises the conversation envelope and a
message-listing read path. Append/update operations on messages arrive
with the streaming turn service in Phase 3.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ..models import ChatConversation, ChatMessage


class ChatRepository(ABC):
    """Abstract base class for chat data access."""

    @abstractmethod
    def get_conversation(self, conversation_id: str) -> ChatConversation | None:
        """Fetch a conversation by id, or None if it doesn't exist."""

    @abstractmethod
    def list_conversations(  # noqa: PLR0913 — keyword-only filter + pagination
        self,
        *,
        patient_id: str,
        owner_user_id: str,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ChatConversation], int]:
        """List conversations matching the filters. Returns (rows, total)."""

    @abstractmethod
    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        """Return all messages for a conversation in ``sequence`` order."""

    @abstractmethod
    def add_conversation(self, conversation: ChatConversation) -> ChatConversation:
        """Insert a new conversation row."""

    @abstractmethod
    def update_conversation(self, conversation: ChatConversation) -> ChatConversation:
        """Persist mutable fields on an existing conversation."""

    @abstractmethod
    def delete_conversation(self, conversation_id: str) -> int:
        """Hard-delete a conversation and its messages. Returns deleted message count."""

    @abstractmethod
    def next_sequence(self, conversation_id: str) -> int:
        """Return the next sequence number for a new message in this conversation.

        Sequences are monotonic per conversation starting at 1. The
        Postgres implementation issues a row-locking SELECT against the
        parent conversation so concurrent ``add_message`` calls are
        serialized per conversation (design doc §14).
        """

    @abstractmethod
    def add_message(self, message: ChatMessage) -> ChatMessage:
        """Append a new chat message row. Called once per user turn and
        once per assistant turn (the latter is later updated in place
        with streaming output and token counts)."""

    @abstractmethod
    def update_message(self, message: ChatMessage) -> ChatMessage:
        """Persist updates to an existing message row.

        Used by the streaming turn service when the assistant message
        completes: content, output_tokens, llm_model, llm_finish_reason,
        and llm_error are filled in after the stream ends.
        """

    @abstractmethod
    def touch_last_turn_at(self, conversation_id: str, last_turn_at: datetime) -> None:
        """Update the conversation's ``last_turn_at`` timestamp.

        Called from the turn service after the assistant row finalizes.
        Idempotent: an out-of-order call with an earlier timestamp is
        silently ignored.
        """


class InMemoryChatRepository(ChatRepository):
    """In-memory ChatRepository for unit tests."""

    def __init__(self) -> None:
        # String values avoid the runtime import the type hints would
        # require — TYPE_CHECKING above keeps the names available for
        # static analysis without re-introducing the import at runtime.
        self._conversations: dict[str, ChatConversation] = {}
        self._messages: dict[str, list[ChatMessage]] = {}

    def get_conversation(self, conversation_id: str) -> ChatConversation | None:
        return self._conversations.get(conversation_id)

    def list_conversations(  # noqa: PLR0913 — keyword-only filter + pagination
        self,
        *,
        patient_id: str,
        owner_user_id: str,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ChatConversation], int]:
        rows = [
            c
            for c in self._conversations.values()
            if c.patient_id == patient_id
            and c.owner_user_id == owner_user_id
            and (caller_feature_key is None or c.caller_feature_key == caller_feature_key)
            and (include_archived or c.archived_at is None)
        ]
        rows.sort(
            key=lambda c: c.last_turn_at or c.created_at,
            reverse=True,
        )
        total = len(rows)
        start = (page - 1) * page_size
        return rows[start : start + page_size], total

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        msgs = list(self._messages.get(conversation_id, []))
        msgs.sort(key=lambda m: m.sequence)
        return msgs

    def add_conversation(self, conversation: ChatConversation) -> ChatConversation:
        self._conversations[conversation.id] = conversation
        self._messages.setdefault(conversation.id, [])
        return conversation

    def update_conversation(self, conversation: ChatConversation) -> ChatConversation:
        self._conversations[conversation.id] = conversation
        return conversation

    def delete_conversation(self, conversation_id: str) -> int:
        msgs = self._messages.pop(conversation_id, [])
        self._conversations.pop(conversation_id, None)
        return len(msgs)

    def next_sequence(self, conversation_id: str) -> int:
        msgs = self._messages.get(conversation_id, [])
        if not msgs:
            return 1
        return max(m.sequence for m in msgs) + 1

    def add_message(self, message: ChatMessage) -> ChatMessage:
        self._messages.setdefault(message.conversation_id, []).append(message)
        return message

    def update_message(self, message: ChatMessage) -> ChatMessage:
        bucket = self._messages.setdefault(message.conversation_id, [])
        for i, existing in enumerate(bucket):
            if existing.id == message.id:
                bucket[i] = message
                return message
        bucket.append(message)
        return message

    def touch_last_turn_at(self, conversation_id: str, last_turn_at: datetime) -> None:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return
        if conv.last_turn_at is None or conv.last_turn_at < last_turn_at:
            conv.last_turn_at = last_turn_at
