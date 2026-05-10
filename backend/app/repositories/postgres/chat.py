# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL implementation of the chat repository."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from ...db.models import ChatConversationRow, ChatMessageRow
from ...models.chat import ChatConversation, ChatMessage
from ..chat import ChatRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresChatRepository(ChatRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Conversations
    # ------------------------------------------------------------------ #

    def get_conversation(self, conversation_id: str) -> ChatConversation | None:
        row = self._session.get(ChatConversationRow, conversation_id)
        return _row_to_conversation(row) if row is not None else None

    def add_conversation(self, conv: ChatConversation) -> ChatConversation:
        row = ChatConversationRow()
        _conversation_to_row(conv, row)
        self._session.add(row)
        self._session.flush()
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
        row = self._session.get(ChatConversationRow, conversation_id)
        if row is None:
            return None
        if title is not None:
            row.title = title
        if default_source_selection is not None:
            row.default_source_selection = default_source_selection
        if clear_archived:
            row.archived_at = None
        elif archived_at is not None:
            row.archived_at = archived_at
        if last_turn_at is not None:
            row.last_turn_at = last_turn_at
        self._session.flush()
        return _row_to_conversation(row)

    def delete_conversation(self, conversation_id: str) -> None:
        row = self._session.get(ChatConversationRow, conversation_id)
        if row is not None:
            self._session.delete(row)
            self._session.flush()

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
        query = self._session.query(ChatConversationRow).filter(
            ChatConversationRow.patient_id == patient_id,
        )
        if owner_user_id:
            query = query.filter(ChatConversationRow.owner_user_id == owner_user_id)
        if caller_feature_key:
            query = query.filter(
                ChatConversationRow.caller_feature_key == caller_feature_key
            )
        if not include_archived:
            query = query.filter(ChatConversationRow.archived_at.is_(None))

        total = query.with_entities(func.count(ChatConversationRow.id)).scalar() or 0
        rows = (
            query.order_by(
                ChatConversationRow.last_turn_at.desc().nullslast(),
                ChatConversationRow.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
            .all()
        )
        return [_row_to_conversation(r) for r in rows], int(total)

    def list_owner_conversations_for_patient(
        self, *, patient_id: str, owner_user_id: str
    ) -> list[ChatConversation]:
        rows = (
            self._session.query(ChatConversationRow)
            .filter(
                ChatConversationRow.patient_id == patient_id,
                ChatConversationRow.owner_user_id == owner_user_id,
            )
            .all()
        )
        return [_row_to_conversation(r) for r in rows]

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        rows = (
            self._session.query(ChatMessageRow)
            .filter(ChatMessageRow.conversation_id == conversation_id)
            .order_by(ChatMessageRow.sequence.asc())
            .all()
        )
        return [_row_to_message(r) for r in rows]

    def next_sequence(self, conversation_id: str) -> int:
        result = self._session.execute(
            select(func.max(ChatMessageRow.sequence)).where(
                ChatMessageRow.conversation_id == conversation_id,
            )
        ).scalar()
        return (result or 0) + 1

    def add_message(self, msg: ChatMessage) -> ChatMessage:
        row = ChatMessageRow()
        _message_to_row(msg, row)
        self._session.add(row)
        self._session.flush()
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
        row = self._session.get(ChatMessageRow, message_id)
        if row is None:
            return None
        row.content = content
        row.input_tokens = input_tokens
        row.output_tokens = output_tokens
        row.llm_model = llm_model
        row.llm_finish_reason = llm_finish_reason
        row.llm_error = llm_error
        self._session.flush()
        return _row_to_message(row)


# --------------------------------------------------------------------------- #
# Mapping helpers
# --------------------------------------------------------------------------- #


def _row_to_conversation(row: ChatConversationRow) -> ChatConversation:
    return ChatConversation(
        id=row.id,
        patient_id=row.patient_id,
        owner_user_id=row.owner_user_id,
        title=row.title,
        caller_system_prompt=row.caller_system_prompt,
        caller_feature_key=row.caller_feature_key,
        default_source_selection=row.default_source_selection or {},
        created_at=row.created_at,
        last_turn_at=row.last_turn_at,
        archived_at=row.archived_at,
    )


def _conversation_to_row(conv: ChatConversation, row: ChatConversationRow) -> None:
    row.id = conv.id
    row.patient_id = conv.patient_id
    row.owner_user_id = conv.owner_user_id
    row.title = conv.title
    row.caller_system_prompt = conv.caller_system_prompt
    row.caller_feature_key = conv.caller_feature_key
    row.default_source_selection = conv.default_source_selection
    row.created_at = conv.created_at
    row.last_turn_at = conv.last_turn_at
    row.archived_at = conv.archived_at


def _row_to_message(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        id=row.id,
        conversation_id=row.conversation_id,
        sequence=row.sequence,
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        created_at=row.created_at,
        source_selection=row.source_selection,
        context_manifest=row.context_manifest,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        llm_model=row.llm_model,
        llm_finish_reason=row.llm_finish_reason,
        llm_error=row.llm_error,
    )


def _message_to_row(msg: ChatMessage, row: ChatMessageRow) -> None:
    row.id = msg.id
    row.conversation_id = msg.conversation_id
    row.sequence = msg.sequence
    row.role = msg.role
    row.content = msg.content
    row.created_at = msg.created_at
    row.source_selection = msg.source_selection
    row.context_manifest = msg.context_manifest
    row.input_tokens = msg.input_tokens
    row.output_tokens = msg.output_tokens
    row.llm_model = msg.llm_model
    row.llm_finish_reason = msg.llm_finish_reason
    row.llm_error = msg.llm_error
