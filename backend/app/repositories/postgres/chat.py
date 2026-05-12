# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL chat repository implementation (THERAPY-tdh)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select

from ...db.models import ChatConversationRow, ChatMessageRow
from ...models import ChatConversation, ChatMessage
from ..chat import ChatRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def _row_to_conversation(row: ChatConversationRow) -> ChatConversation:
    return ChatConversation(
        id=row.id,
        patient_id=row.patient_id,
        owner_user_id=row.owner_user_id,
        title=row.title,
        caller_system_prompt=row.caller_system_prompt,
        caller_feature_key=row.caller_feature_key,
        default_source_selection=row.default_source_selection,
        created_at=row.created_at,
        last_turn_at=row.last_turn_at,
        archived_at=row.archived_at,
    )


def _row_to_message(row: ChatMessageRow) -> ChatMessage:
    return ChatMessage(
        id=row.id,
        conversation_id=row.conversation_id,
        sequence=row.sequence,
        role=row.role,
        content=row.content,
        source_selection=row.source_selection,
        context_manifest=row.context_manifest,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        llm_model=row.llm_model,
        llm_finish_reason=row.llm_finish_reason,
        llm_error=row.llm_error,
        created_at=row.created_at,
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


class PostgresChatRepository(ChatRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_conversation(self, conversation_id: str) -> ChatConversation | None:
        row = self._session.get(ChatConversationRow, conversation_id)
        return _row_to_conversation(row) if row is not None else None

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
        query = self._session.query(ChatConversationRow).filter(
            ChatConversationRow.patient_id == patient_id,
            ChatConversationRow.owner_user_id == owner_user_id,
        )
        if caller_feature_key is not None:
            query = query.filter(ChatConversationRow.caller_feature_key == caller_feature_key)
        if not include_archived:
            query = query.filter(ChatConversationRow.archived_at.is_(None))

        # ORDER BY last_turn_at DESC NULLS LAST, created_at DESC. Postgres
        # treats DESC NULLS as LAST by default, so an explicit clause is
        # not required, but spell it out for portability across dialects
        # used in dev/test.
        query = query.order_by(
            ChatConversationRow.last_turn_at.desc().nullslast(),
            ChatConversationRow.created_at.desc(),
        )

        total = query.count()
        offset = (page - 1) * page_size
        rows = query.offset(offset).limit(page_size).all()
        return [_row_to_conversation(r) for r in rows], total

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        stmt = (
            select(ChatMessageRow)
            .where(ChatMessageRow.conversation_id == conversation_id)
            .order_by(ChatMessageRow.sequence.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [_row_to_message(r) for r in rows]

    def add_conversation(self, conversation: ChatConversation) -> ChatConversation:
        row = ChatConversationRow()
        _conversation_to_row(conversation, row)
        self._session.add(row)
        self._session.flush()
        return conversation

    def update_conversation(self, conversation: ChatConversation) -> ChatConversation:
        row = self._session.get(ChatConversationRow, conversation.id)
        if row is None:
            # Caller is responsible for ensuring the row exists before
            # update; the route layer always reads-then-writes.
            row = ChatConversationRow()
            self._session.add(row)
        _conversation_to_row(conversation, row)
        self._session.flush()
        return conversation

    def delete_conversation(self, conversation_id: str) -> int:
        # Count messages before delete so the caller can surface it
        # (e.g. in the audit log payload).
        count = (
            self._session.query(ChatMessageRow)
            .filter(ChatMessageRow.conversation_id == conversation_id)
            .count()
        )
        # The FK has ON DELETE CASCADE; the explicit message delete is a
        # belt-and-suspenders guard for backends or test doubles where the
        # cascade isn't enforced.
        self._session.execute(
            delete(ChatMessageRow).where(ChatMessageRow.conversation_id == conversation_id)
        )
        self._session.execute(
            delete(ChatConversationRow).where(ChatConversationRow.id == conversation_id)
        )
        self._session.flush()
        return count
