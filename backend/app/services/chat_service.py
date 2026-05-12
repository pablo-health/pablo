# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat conversation service (THERAPY-tdh, Phase 1 of THERAPY-bhv).

Conversation lifecycle only — create, get, list, patch, hard-delete.
The streaming turn surface lives in Phase 3 (THERAPY-5x5).

Authorization is the route layer's job: routes use the existing patient
ACL (``PatientRepository.get(patient_id, user_id)``) before invoking
this service. The service trusts callers to pass an authorized
``patient`` / ``owner_user_id`` pair.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from ..models import ChatConversation
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..models import ChatMessage
    from ..repositories import ChatRepository


class ChatConversationNotFoundError(Exception):
    """Raised when a conversation cannot be located."""


class ChatService:
    """Business logic for chat conversation lifecycle."""

    def __init__(self, repo: ChatRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_conversation(self, conversation_id: str) -> ChatConversation:
        conv = self._repo.get_conversation(conversation_id)
        if conv is None:
            raise ChatConversationNotFoundError(conversation_id)
        return conv

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        return self._repo.list_messages(conversation_id)

    def list_conversations(
        self,
        *,
        patient_id: str,
        owner_user_id: str,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ChatConversation], int]:
        return self._repo.list_conversations(
            patient_id=patient_id,
            owner_user_id=owner_user_id,
            caller_feature_key=caller_feature_key,
            include_archived=include_archived,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create_conversation(
        self,
        *,
        patient_id: str,
        owner_user_id: str,
        caller_feature_key: str,
        caller_system_prompt: str,
        title: str | None,
        default_source_selection: dict[str, Any] | None,
        patient_display_name: str | None = None,
    ) -> ChatConversation:
        """Create and persist a new conversation envelope.

        ``patient_display_name`` is used only to seed a fallback title;
        it is **not** stored on the conversation row (the title field
        is). Callers should pass the patient's display name when they
        have it from the auth check; if absent, a generic title is used.
        """
        resolved_title = (title or "").strip()
        if not resolved_title:
            if patient_display_name:
                resolved_title = f"Chat about {patient_display_name}"
            else:
                resolved_title = "Patient chat"
            resolved_title = resolved_title[:200]

        conversation = ChatConversation(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            owner_user_id=owner_user_id,
            title=resolved_title,
            caller_system_prompt=caller_system_prompt,
            caller_feature_key=caller_feature_key,
            default_source_selection=default_source_selection,
            created_at=utc_now(),
        )
        return self._repo.add_conversation(conversation)

    def update_conversation(
        self,
        conversation_id: str,
        *,
        title: str | None = None,
        default_source_selection: dict[str, Any] | None = None,
        archive: bool | None = None,
    ) -> ChatConversation:
        """Apply mutable-field updates. Returns the post-update conversation.

        Per design doc §6.5, only ``title``, ``default_source_selection``,
        and ``archived_at`` (via ``archive``) are mutable. The route layer
        decides what comes in; this method just applies it.
        """
        conv = self.get_conversation(conversation_id)
        if title is not None:
            conv.title = title.strip()[:200] or conv.title
        if default_source_selection is not None:
            conv.default_source_selection = default_source_selection
        if archive is True and conv.archived_at is None:
            conv.archived_at = utc_now()
        elif archive is False:
            conv.archived_at = None
        return self._repo.update_conversation(conv)

    def delete_conversation(self, conversation_id: str) -> int:
        """Hard-delete a conversation and its messages. Returns msg count.

        Designed for §6.6 ``mode=purge`` (the default). Soft-delete
        (``mode=archive``) is handled via ``update_conversation`` with
        ``archive=True``.
        """
        return self._repo.delete_conversation(conversation_id)
