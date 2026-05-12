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
