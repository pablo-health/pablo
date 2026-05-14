# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat conversation service.

Conversation lifecycle — create, get, list, patch, hard-delete. The
streaming turn surface lives in :mod:`chat_turn_service`.

Authorization is the route layer's job at the patient level
(``PatientRepository.get(patient_id, user_id)``); this service threads
``user_id`` through to the repository so reads/writes resolve through
the same ``has_patient_access`` grant table that scopes notes and
sessions.

The ``owner_user_id`` field on a conversation is actor data ("who
started this chat") — it is *not* an access proxy. Co-treating /
covering / successor clinicians can read and continue any chat
referencing a patient they have a grant on, regardless of who started
it. See ``alembic/versions/3f8d1a6c2b04_*.py`` for the migration that
swaps the RLS policy.
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
    """Raised when a conversation cannot be located or accessed.

    The repository returns ``None`` indistinguishably for "row absent"
    and "user lacks grant", so this single error covers both cases —
    the route layer translates it to a 404 either way.
    """


class ChatService:
    """Business logic for chat conversation lifecycle."""

    def __init__(self, repo: ChatRepository) -> None:
        self._repo = repo

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> ChatConversation:
        conv = self._repo.get_conversation(conversation_id, user_id)
        if conv is None:
            raise ChatConversationNotFoundError(conversation_id)
        return conv

    def list_messages(
        self, conversation_id: str, user_id: str
    ) -> list[ChatMessage]:
        return self._repo.list_messages(conversation_id, user_id)

    def list_conversations(
        self,
        *,
        patient_id: str,
        user_id: str,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ChatConversation], int]:
        return self._repo.list_conversations(
            patient_id=patient_id,
            user_id=user_id,
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

        ``owner_user_id`` is the creating clinician — recorded on the
        row as actor data and reused as the access-check identity for
        the insert (a clinician creating a chat about a patient must
        have a grant on that patient; the route layer already verified
        this via ``patient_repo.get``).

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
        return self._repo.add_conversation(conversation, owner_user_id)

    def update_conversation(
        self,
        conversation_id: str,
        user_id: str,
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
        conv = self.get_conversation(conversation_id, user_id)
        if title is not None:
            conv.title = title.strip()[:200] or conv.title
        if default_source_selection is not None:
            conv.default_source_selection = default_source_selection
        if archive is True and conv.archived_at is None:
            conv.archived_at = utc_now()
        elif archive is False:
            conv.archived_at = None
        return self._repo.update_conversation(conv, user_id)

    def delete_conversation(self, conversation_id: str, user_id: str) -> int:
        """Hard-delete a conversation and its messages. Returns msg count.

        Designed for §6.6 ``mode=purge`` (the default). Soft-delete
        (``mode=archive``) is handled via ``update_conversation`` with
        ``archive=True``.
        """
        return self._repo.delete_conversation(conversation_id, user_id)
