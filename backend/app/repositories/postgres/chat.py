# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL chat repository implementation.

Access is gated by ``patient_clinicians`` grants on the conversation's
patient, mirroring the session / note / appointment pattern from
PR #170. ``ChatConversationRow.owner_user_id`` is kept as actor data
("who started this chat") but is *not* the authorization proxy.

Reads return ``None`` / empty list when the caller has no grant —
indistinguishable from "row absent" so the surface does not leak an
existence oracle. Writes raise :class:`PatientAccessDeniedError` via
the abstract repo contract.

The join through :class:`PatientClinicianRow` happens in the same SQL
statement as the read where possible — same idiom as
:class:`PostgresNotesRepository.get` so the DB can short-circuit on
``EXISTS``. The Postgres RLS policy on ``chat_conversations`` provides
a defense-in-depth gate (installed by ``enable_rls_on_schema`` on the
next bootstrap pass; see migration ``3f8d1a6c2b04``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, func, or_, select, text

from ...db.models import ChatConversationRow, ChatMessageRow, PatientClinicianRow
from ...models import ChatConversation, ChatMessage
from ...utcnow import utc_now
from ..chat import ChatRepository
from ..note import PatientAccessDeniedError

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session


def _grant_filters(user_id: str) -> tuple:
    """Predicates for "user has a non-expired grant on the joined row's patient_id".

    Same shape as :func:`postgres.session._grant_filters` — keeping the
    two helpers separate (rather than hoisting to a shared module) so
    each repository stays self-contained and tests can read it
    end-to-end without cross-file jumps.
    """
    return (
        PatientClinicianRow.user_id == user_id,
        or_(
            PatientClinicianRow.expires_at.is_(None),
            PatientClinicianRow.expires_at > utc_now(),
        ),
    )


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


def _message_to_row(message: ChatMessage, row: ChatMessageRow) -> None:
    row.id = message.id
    row.conversation_id = message.conversation_id
    row.sequence = message.sequence
    row.role = message.role
    row.content = message.content
    row.source_selection = message.source_selection
    row.context_manifest = message.context_manifest
    row.input_tokens = message.input_tokens
    row.output_tokens = message.output_tokens
    row.llm_model = message.llm_model
    row.llm_finish_reason = message.llm_finish_reason
    row.llm_error = message.llm_error
    row.created_at = message.created_at


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

    # --- internal access predicate ---

    def _has_access(self, patient_id: str, user_id: str) -> bool:
        """Application-layer mirror of the RLS policy.

        Same idiom as :class:`PostgresNotesRepository._has_access`: call
        the SQL function rather than re-implementing the
        ``patient_clinicians`` lookup in Python so app-layer and
        DB-layer authorization stay in lockstep.
        """
        result = self._session.execute(
            text("SELECT has_patient_access(CAST(:pid AS uuid), :uid)"),
            {"pid": patient_id, "uid": user_id},
        ).scalar()
        return bool(result)

    # --- reads ---

    def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> ChatConversation | None:
        row = (
            self._session.query(ChatConversationRow)
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == ChatConversationRow.patient_id,
            )
            .filter(
                ChatConversationRow.id == conversation_id,
                *_grant_filters(user_id),
            )
            .one_or_none()
        )
        return _row_to_conversation(row) if row is not None else None

    def list_conversations(  # noqa: PLR0913 — keyword-only filter + pagination
        self,
        *,
        patient_id: str,
        user_id: str,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ChatConversation], int]:
        # Top-level access gate: deny → empty result, no row enumeration.
        # Mirrors PostgresNotesRepository.list_by_patient — avoids
        # leaking a has-conversations-vs-no-conversations signal via
        # timing.
        if not self._has_access(patient_id, user_id):
            return [], 0

        query = self._session.query(ChatConversationRow).filter(
            ChatConversationRow.patient_id == patient_id,
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

    def list_messages(
        self, conversation_id: str, user_id: str
    ) -> list[ChatMessage]:
        # Join through the parent conversation + patient_clinicians so a
        # caller without a grant sees an empty list regardless of
        # whether the conversation exists. One SQL round trip; no
        # existence oracle.
        stmt = (
            select(ChatMessageRow)
            .join(
                ChatConversationRow,
                ChatConversationRow.id == ChatMessageRow.conversation_id,
            )
            .join(
                PatientClinicianRow,
                PatientClinicianRow.patient_id == ChatConversationRow.patient_id,
            )
            .where(
                ChatMessageRow.conversation_id == conversation_id,
                *_grant_filters(user_id),
            )
            .order_by(ChatMessageRow.sequence.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [_row_to_message(r) for r in rows]

    # --- writes ---

    def add_conversation(
        self, conversation: ChatConversation, user_id: str
    ) -> ChatConversation:
        if not self._has_access(conversation.patient_id, user_id):
            raise PatientAccessDeniedError(conversation.patient_id, user_id)
        row = ChatConversationRow()
        _conversation_to_row(conversation, row)
        self._session.add(row)
        self._session.flush()
        return conversation

    def update_conversation(
        self, conversation: ChatConversation, user_id: str
    ) -> ChatConversation:
        if not self._has_access(conversation.patient_id, user_id):
            raise PatientAccessDeniedError(conversation.patient_id, user_id)
        row = self._session.get(ChatConversationRow, conversation.id)
        if row is None:
            # Route layer always reads-then-writes, but handle the
            # upsert-style fallback to match the pre-#170 contract.
            row = ChatConversationRow()
            self._session.add(row)
        elif not self._has_access(row.patient_id, user_id):
            # Defense-in-depth: if the on-disk patient_id differs from
            # the in-memory conversation (e.g. caller forged it), check
            # the DB-side value too. Either grant denies the write.
            raise PatientAccessDeniedError(row.patient_id, user_id)
        _conversation_to_row(conversation, row)
        self._session.flush()
        return conversation

    def next_sequence(self, conversation_id: str) -> int:
        # Lock the parent conversation row so concurrent message inserts
        # against the same conversation serialize on the same lock — see
        # design doc §14. The lock is released when the surrounding
        # request transaction commits.
        self._session.execute(
            select(ChatConversationRow.id)
            .where(ChatConversationRow.id == conversation_id)
            .with_for_update()
        ).scalar_one_or_none()
        current_max = self._session.execute(
            select(func.max(ChatMessageRow.sequence)).where(
                ChatMessageRow.conversation_id == conversation_id
            )
        ).scalar_one_or_none()
        return (current_max or 0) + 1

    def add_message(self, message: ChatMessage) -> ChatMessage:
        row = ChatMessageRow()
        _message_to_row(message, row)
        self._session.add(row)
        self._session.flush()
        return message

    def update_message(self, message: ChatMessage) -> ChatMessage:
        row = self._session.get(ChatMessageRow, message.id)
        if row is None:
            row = ChatMessageRow()
            self._session.add(row)
        _message_to_row(message, row)
        self._session.flush()
        return message

    def touch_last_turn_at(self, conversation_id: str, last_turn_at: datetime) -> None:
        row = self._session.get(ChatConversationRow, conversation_id)
        if row is None:
            return
        if row.last_turn_at is None or row.last_turn_at < last_turn_at:
            row.last_turn_at = last_turn_at
            self._session.flush()

    def delete_conversation(self, conversation_id: str, user_id: str) -> int:
        # Read-then-delete so the access check uses the on-disk
        # patient_id. Mirrors the soft-fail contract on
        # NotesRepository.delete: missing row OR no grant → return 0
        # (treat as "nothing to do") without telling the caller which.
        row = self._session.get(ChatConversationRow, conversation_id)
        if row is None or not self._has_access(row.patient_id, user_id):
            return 0
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
