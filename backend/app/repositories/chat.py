# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat conversation + message repository contracts.

Access is gated by ``has_patient_access(patient_id, user_id)`` — the
same grant table (``patient_clinicians``) that scopes patients, notes,
sessions, and appointments. ``ChatConversation.owner_user_id`` is
retained on the row as actor data ("who started this chat"); it is not
the access proxy. The semantic mirrors ``therapy_sessions.user_id``
after PR #170.

Two clinical invariants this enforces:

  1. **Continuity across transfer / coverage.** A covering or
     successor clinician inherits the chat history their predecessor
     accumulated about the shared patient — no "start from scratch"
     gap.

  2. **§ 164.312(a)(1) minimum-necessary.** When a clinician loses
     the treatment relationship, they simultaneously lose access to
     chats referencing the patient's PHI.

Reads return ``None`` / empty list when the caller has no grant —
matches the empty-result shape for "doesn't exist" so callers can't
distinguish absent from forbidden via timing or status code (no
existence oracle). Writes raise :class:`PatientAccessDeniedError`
because silently no-op'ing a write would mask broken code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from .note import PatientAccessDeniedError

if TYPE_CHECKING:
    from datetime import datetime

    from ..models import ChatConversation, ChatMessage


class ChatRepository(ABC):
    """Abstract base class for chat data access.

    Every method that touches a conversation takes the requesting
    user's id so the repository can resolve access through
    :func:`has_patient_access` (Postgres) or the equivalent
    ``(patient_id, user_id)`` access set (in-memory). The
    ``owner_user_id`` on the conversation row is *not* consulted for
    authorization — it is preserved purely as audit / display data.
    """

    @abstractmethod
    def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> ChatConversation | None:
        """Fetch a conversation if accessible, else ``None``.

        Returns ``None`` indistinguishably for "doesn't exist" and
        "user has no grant on the conversation's patient" — the
        existence-oracle guard. Mirrors
        :class:`NotesRepository.get` from PR #170.
        """

    @abstractmethod
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
        """List conversations for a patient. Returns ``([], 0)`` when access denied."""

    @abstractmethod
    def list_messages(
        self, conversation_id: str, user_id: str
    ) -> list[ChatMessage]:
        """Return all messages for an accessible conversation.

        ``[]`` if the conversation does not exist or the caller has no
        grant on its patient — matches the read contract on
        :meth:`get_conversation`.
        """

    @abstractmethod
    def list_messages_windowed(
        self, conversation_id: str, user_id: str, *, head: int = 2, tail: int = 30
    ) -> list[ChatMessage]:
        """Return a windowed slice of a conversation's messages.

        Pins the first ``head`` messages (the opening turn, which sets
        the conversation's frame) and the most-recent ``tail`` messages
        (the live dialogue), dropping the middle when the conversation
        is longer than ``head + tail``. This is the hot-path read for
        the streaming turn service: an unbounded :meth:`list_messages`
        grows linearly with conversation length on *every* turn.

        Both ends are index-range scans on the
        ``(conversation_id, sequence)`` unique index, merged + deduped
        by sequence and returned in ascending sequence order. Same
        access contract as :meth:`list_messages`: ``[]`` when the
        conversation is absent or the caller has no grant — no existence
        oracle.
        """

    @abstractmethod
    def add_conversation(
        self, conversation: ChatConversation, user_id: str
    ) -> ChatConversation:
        """Insert a new conversation row.

        Raises :class:`PatientAccessDeniedError` if ``user_id`` has no
        grant on ``conversation.patient_id`` — defense-in-depth; the
        route layer's :func:`patient_repo.get` check is the primary
        gate.
        """

    @abstractmethod
    def update_conversation(
        self, conversation: ChatConversation, user_id: str
    ) -> ChatConversation:
        """Persist mutable fields. Raises :class:`PatientAccessDeniedError` if blocked."""

    @abstractmethod
    def delete_conversation(self, conversation_id: str, user_id: str) -> int:
        """Hard-delete a conversation and its messages.

        Returns the message count on success, ``0`` if the conversation
        is missing or the caller has no grant — matches the soft-fail
        contract on :meth:`NotesRepository.delete`.
        """

    @abstractmethod
    def next_sequence(self, conversation_id: str) -> int:
        """Return the next sequence number for a new message in this conversation.

        Sequences are monotonic per conversation starting at 1. The
        Postgres implementation issues a row-locking SELECT against the
        parent conversation so concurrent ``add_message`` calls are
        serialized per conversation (design doc §14).

        No ``user_id`` argument — this is an internal helper called by
        the turn service *after* the route layer has already
        authorized. Threading an access check here would force a
        redundant lookup on every message and would still depend on
        the calling service to have done the real check first.
        """

    @abstractmethod
    def add_message(self, message: ChatMessage) -> ChatMessage:
        """Append a new chat message row.

        See :meth:`next_sequence` for the rationale on not taking a
        ``user_id`` — the caller (turn service) has already authorized
        against the parent conversation.
        """

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


_TEST_DEFAULT_USER = "__inmemory_test_default__"


class InMemoryChatRepository(ChatRepository):
    """In-memory ChatRepository for unit tests.

    Maintains a ``(patient_id, user_id)`` access set populated via
    :meth:`grant_access`. Tests that don't care about access control
    can call :meth:`grant_all_access` (which the shared
    ``mock_chat_repo`` fixture in ``conftest.py`` does *not* enable by
    default — chat tests are explicit because the cross-clinician
    invariants are the whole point of this repo).

    The ``user_id`` parameter defaults to a sentinel on every method
    purely as a test ergonomic; production code paths thread
    ``user_id`` explicitly. The :class:`PostgresChatRepository`
    intentionally does *not* default, so prod can't accidentally drop
    the argument.
    """

    def __init__(self) -> None:
        self._conversations: dict[str, ChatConversation] = {}
        self._messages: dict[str, list[ChatMessage]] = {}
        self._access: set[tuple[str, str]] = set()  # (patient_id, user_id)
        self._allow_all = False

    # --- test setup helpers (mirror has_patient_access semantics) ---

    def grant_access(self, patient_id: str, user_id: str) -> None:
        """Record that ``user_id`` may read/write ``patient_id``'s chats."""
        self._access.add((patient_id, user_id))

    def revoke_access(self, patient_id: str, user_id: str) -> None:
        """Drop a previously granted ``(patient_id, user_id)`` pair.

        Used by the post-transfer regression test to simulate a patient
        being moved from one clinician to another — drop A's row from
        ``patient_clinicians`` (this), insert B's (:meth:`grant_access`),
        then assert that A's reads return None and B's succeed.
        """
        self._access.discard((patient_id, user_id))

    def grant_all_access(self) -> None:
        """Open the gate — only for legacy tests that pre-date the access model."""
        self._allow_all = True

    def _can_access(self, patient_id: str, user_id: str) -> bool:
        return self._allow_all or (patient_id, user_id) in self._access

    # --- reads ---

    def get_conversation(
        self, conversation_id: str, user_id: str = _TEST_DEFAULT_USER
    ) -> ChatConversation | None:
        conv = self._conversations.get(conversation_id)
        if conv is None:
            return None
        if not self._can_access(conv.patient_id, user_id):
            return None
        return conv

    def list_conversations(  # noqa: PLR0913 — keyword-only filter + pagination
        self,
        *,
        patient_id: str,
        user_id: str = _TEST_DEFAULT_USER,
        caller_feature_key: str | None = None,
        include_archived: bool = False,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[ChatConversation], int]:
        if not self._can_access(patient_id, user_id):
            return [], 0
        rows = [
            c
            for c in self._conversations.values()
            if c.patient_id == patient_id
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

    def list_messages(
        self, conversation_id: str, user_id: str = _TEST_DEFAULT_USER
    ) -> list[ChatMessage]:
        conv = self._conversations.get(conversation_id)
        if conv is None or not self._can_access(conv.patient_id, user_id):
            return []
        msgs = list(self._messages.get(conversation_id, []))
        msgs.sort(key=lambda m: m.sequence)
        return msgs

    def list_messages_windowed(
        self,
        conversation_id: str,
        user_id: str = _TEST_DEFAULT_USER,
        *,
        head: int = 2,
        tail: int = 30,
    ) -> list[ChatMessage]:
        if head < 0 or tail < 0:
            raise ValueError("head and tail must be non-negative")
        conv = self._conversations.get(conversation_id)
        if conv is None or not self._can_access(conv.patient_id, user_id):
            return []
        msgs = sorted(self._messages.get(conversation_id, []), key=lambda m: m.sequence)
        head_rows = msgs[:head] if head else []
        tail_rows = msgs[-tail:] if tail else []
        by_seq: dict[int, ChatMessage] = {}
        for m in (*head_rows, *tail_rows):
            by_seq[m.sequence] = m
        return [by_seq[s] for s in sorted(by_seq)]

    # --- writes ---

    def add_conversation(
        self, conversation: ChatConversation, user_id: str = _TEST_DEFAULT_USER
    ) -> ChatConversation:
        if not self._can_access(conversation.patient_id, user_id):
            raise PatientAccessDeniedError(conversation.patient_id, user_id)
        self._conversations[conversation.id] = conversation
        self._messages.setdefault(conversation.id, [])
        return conversation

    def update_conversation(
        self, conversation: ChatConversation, user_id: str = _TEST_DEFAULT_USER
    ) -> ChatConversation:
        if not self._can_access(conversation.patient_id, user_id):
            raise PatientAccessDeniedError(conversation.patient_id, user_id)
        self._conversations[conversation.id] = conversation
        return conversation

    def delete_conversation(
        self, conversation_id: str, user_id: str = _TEST_DEFAULT_USER
    ) -> int:
        conv = self._conversations.get(conversation_id)
        if conv is None or not self._can_access(conv.patient_id, user_id):
            return 0
        msgs = self._messages.pop(conversation_id, [])
        self._conversations.pop(conversation_id, None)
        return len(msgs)

    # --- message-level helpers (caller-authorized; see ChatRepository) ---

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
