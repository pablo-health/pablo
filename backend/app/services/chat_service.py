# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat-conversation service.

Owns conversation lifecycle (create/get/list/update/archive/purge) and
the per-turn pipeline (assemble bundle → call LLM → persist turns →
audit). Streaming I/O lives in ``ChatTurnStreamer``; this module
exposes the synchronous control plane.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from ..models.chat import ChatConversation, ChatMessage
from ..settings import get_settings
from ..utcnow import utc_now
from .chat_context_bundler import (
    DEFAULT_SOURCE_SELECTION,
    BundlerDeps,
    assemble_context_bundle,
    estimate_tokens,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from ..repositories.chat import ChatRepository
    from ..repositories.note import NotesRepository
    from ..repositories.patient import PatientRepository
    from .chat_context_bundler import ContextBundle

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class ChatConversationNotFoundError(Exception):
    pass


class ChatPatientGoneError(Exception):
    """The conversation's patient was hard-deleted; surfaces as 410."""


class ChatPermissionError(Exception):
    pass


class ChatPatientNotFoundError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Streaming gateway protocol
# --------------------------------------------------------------------------- #


@dataclass
class StreamedChunk:
    text: str


@dataclass
class StreamResult:
    content: str
    output_tokens: int
    finish_reason: str
    error: str | None = None


class LlmStreamGateway(Protocol):
    """Streaming-capable LLM gateway.

    Implementations yield ``StreamedChunk`` objects token-by-token and
    return a ``StreamResult`` on completion. The default
    implementation wraps the existing non-streaming LLM client and
    yields one chunk for the whole response — functional but
    non-incremental — see ``BufferedLlmGateway`` below.
    """

    def stream(
        self,
        *,
        prompt: str,
        model: str,
    ) -> Iterator[StreamedChunk] | AsyncIterator[StreamedChunk]: ...

    def finish(self) -> StreamResult: ...


class BufferedLlmGateway:
    """Synchronous fallback that calls the existing LLM client and
    emits the full response as a single chunk.

    Self-hosters who want true token-by-token streaming swap this for
    a provider-specific gateway. The persistence + audit + manifest
    pipeline behaves identically either way.
    """

    def __init__(self, model: str) -> None:
        self._model = model
        self._final: StreamResult | None = None

    def stream(self, *, prompt: str, model: str) -> Iterator[StreamedChunk]:
        del model  # gateway is constructed with the model id; param kept for protocol parity
        try:
            from meeting_transcription.utils.llm_client import LLMClient

            client = LLMClient()
            response_text = client.call(prompt=prompt, max_tokens=2000, temperature=0.4)
        except Exception as exc:
            self._final = StreamResult(
                content="",
                output_tokens=0,
                finish_reason="error",
                error=type(exc).__name__,
            )
            return
        self._final = StreamResult(
            content=response_text,
            output_tokens=estimate_tokens(response_text),
            finish_reason="stop",
        )
        # One chunk for the whole response — true streaming requires a
        # provider-specific gateway implementation.
        yield StreamedChunk(text=response_text)

    def finish(self) -> StreamResult:
        if self._final is None:
            return StreamResult(
                content="",
                output_tokens=0,
                finish_reason="error",
                error="no_stream",
            )
        return self._final


def get_default_llm_gateway(model: str) -> LlmStreamGateway:
    return BufferedLlmGateway(model=model)


# --------------------------------------------------------------------------- #
# Service
# --------------------------------------------------------------------------- #


class ChatService:
    def __init__(
        self,
        *,
        chat_repo: ChatRepository,
        notes_repo: NotesRepository,
        patient_repo: PatientRepository,
    ) -> None:
        self._chat_repo = chat_repo
        self._notes_repo = notes_repo
        self._patient_repo = patient_repo

    # ------------------------------------------------------------------ #
    # Conversation lifecycle
    # ------------------------------------------------------------------ #

    def create_conversation(
        self,
        *,
        owner_user_id: str,
        patient_id: str,
        caller_feature_key: str,
        caller_system_prompt: str,
        title: str | None,
        default_source_selection: dict[str, Any] | None,
    ) -> ChatConversation:
        settings = get_settings()
        prompt = caller_system_prompt.strip()
        if not prompt:
            raise ValueError("caller_system_prompt must not be empty")
        if len(prompt) > settings.chat_max_system_prompt_chars:
            raise ValueError(
                f"caller_system_prompt exceeds {settings.chat_max_system_prompt_chars} chars"
            )

        patient = self._patient_repo.get(patient_id, owner_user_id)
        if patient is None:
            raise ChatPatientNotFoundError(patient_id)

        title_value = title or f"Chat about {patient.first_name} {patient.last_name}"
        title_value = title_value[:200] or "Chat"

        conv = ChatConversation(
            id=str(uuid.uuid4()),
            patient_id=patient.id,
            owner_user_id=owner_user_id,
            title=title_value,
            caller_system_prompt=prompt,
            caller_feature_key=caller_feature_key,
            default_source_selection=default_source_selection or DEFAULT_SOURCE_SELECTION,
            created_at=utc_now(),
        )
        return self._chat_repo.add_conversation(conv)

    def get_conversation(
        self, conversation_id: str, *, requesting_user_id: str
    ) -> ChatConversation:
        conv = self._chat_repo.get_conversation(conversation_id)
        if conv is None:
            raise ChatConversationNotFoundError(conversation_id)
        if conv.owner_user_id != requesting_user_id:
            raise ChatPermissionError(conversation_id)
        return conv

    def list_messages(self, conversation_id: str) -> list[ChatMessage]:
        return self._chat_repo.list_messages(conversation_id)

    def list_conversations(
        self,
        *,
        patient_id: str,
        owner_user_id: str,
        caller_feature_key: str | None,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> tuple[list[ChatConversation], int]:
        return self._chat_repo.list_conversations(
            patient_id=patient_id,
            owner_user_id=owner_user_id,
            caller_feature_key=caller_feature_key,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )

    def update_conversation(
        self,
        conversation_id: str,
        *,
        requesting_user_id: str,
        title: str | None = None,
        default_source_selection: dict[str, Any] | None = None,
        archive: bool | None = None,
    ) -> ChatConversation:
        conv = self.get_conversation(conversation_id, requesting_user_id=requesting_user_id)
        archived_at = None
        clear_archived = False
        if archive is True and conv.archived_at is None:
            archived_at = utc_now()
        elif archive is False and conv.archived_at is not None:
            clear_archived = True
        updated = self._chat_repo.update_conversation(
            conversation_id,
            title=title,
            default_source_selection=default_source_selection,
            archived_at=archived_at,
            clear_archived=clear_archived,
        )
        if updated is None:
            # get_conversation above just confirmed the row existed; a race here
            # would mean another request purged between the read and the update.
            raise ChatConversationNotFoundError(conversation_id)
        return updated

    def purge_conversation(
        self, conversation_id: str, *, requesting_user_id: str
    ) -> tuple[ChatConversation, int]:
        """Hard-delete the conversation and its messages.

        Returns the conversation snapshot and the count of purged
        messages so the route can audit the action without leaking
        content.
        """
        conv = self.get_conversation(
            conversation_id, requesting_user_id=requesting_user_id
        )
        messages = self._chat_repo.list_messages(conversation_id)
        self._chat_repo.delete_conversation(conversation_id)
        return conv, len(messages)

    def purge_owner_conversations_for_patient(
        self, *, patient_id: str, owner_user_id: str
    ) -> int:
        """Bulk-purge: every conversation the caller owns for ``patient_id``.

        Returns the count of conversations purged.
        """
        rows = self._chat_repo.list_owner_conversations_for_patient(
            patient_id=patient_id, owner_user_id=owner_user_id
        )
        for conv in rows:
            self._chat_repo.delete_conversation(conv.id)
        return len(rows)

    # ------------------------------------------------------------------ #
    # Turn lifecycle
    # ------------------------------------------------------------------ #

    def begin_turn(
        self,
        *,
        conversation_id: str,
        requesting_user_id: str,
        content: str,
        source_selection: dict[str, Any] | None,
    ) -> tuple[ChatConversation, ChatMessage, ChatMessage, ContextBundle]:
        """Persist the user turn + a placeholder assistant turn and
        return the assembled context bundle so the caller can stream
        the LLM response.

        The placeholder assistant row is inserted with empty content
        and finalized via ``finalize_turn`` at end-of-stream.
        """
        conv = self.get_conversation(conversation_id, requesting_user_id=requesting_user_id)
        if conv.archived_at is not None:
            raise ValueError("conversation is archived")

        patient = self._patient_repo.get(conv.patient_id, requesting_user_id)
        if patient is None:
            # Patient was hard-deleted; auto-archive the conversation.
            self._chat_repo.update_conversation(
                conv.id, archived_at=utc_now()
            )
            raise ChatPatientGoneError(conv.patient_id)

        active_selection = source_selection or conv.default_source_selection
        deps = BundlerDeps(notes_repo=self._notes_repo)
        bundle = assemble_context_bundle(
            deps=deps,
            patient_id=conv.patient_id,
            selection=active_selection,
            token_budget=get_settings().chat_token_budget,
        )

        prior = self._chat_repo.list_messages(conv.id)

        next_seq = self._chat_repo.next_sequence(conv.id)
        user_msg = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            sequence=next_seq,
            role="user",
            content=content,
            created_at=utc_now(),
            source_selection=active_selection,
            context_manifest=bundle.manifest(),
        )
        self._chat_repo.add_message(user_msg)

        assistant_msg = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=conv.id,
            sequence=next_seq + 1,
            role="assistant",
            content="",
            created_at=utc_now(),
        )
        self._chat_repo.add_message(assistant_msg)

        # Re-stamp prior list with the user/assistant additions so callers
        # can build the LLM prompt envelope without re-querying.
        prior.extend([user_msg, assistant_msg])
        return conv, user_msg, assistant_msg, bundle

    def finalize_turn(
        self,
        assistant_message_id: str,
        *,
        content: str,
        input_tokens: int,
        output_tokens: int,
        llm_model: str,
        llm_finish_reason: str,
        llm_error: str | None,
    ) -> ChatMessage | None:
        msg = self._chat_repo.finalize_assistant_message(
            assistant_message_id,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            llm_model=llm_model,
            llm_finish_reason=llm_finish_reason,
            llm_error=llm_error,
        )
        if msg is not None:
            self._chat_repo.update_conversation(msg.conversation_id, last_turn_at=utc_now())
        return msg


# --------------------------------------------------------------------------- #
# Prompt envelope construction (kept as a module-level pure function so
# tests can assert envelope ordering without standing up the service)
# --------------------------------------------------------------------------- #


def build_prompt_envelope(
    *,
    caller_system_prompt: str,
    bundle_text: str,
    prior_messages: list[ChatMessage],
    new_user_message: str,
) -> str:
    """Concatenate the LLM prompt in the canonical order.

    The OSS primitive never injects clinical guidance into this
    envelope. Callers that want clinical opinion put it in
    ``caller_system_prompt``.
    """
    parts: list[str] = [caller_system_prompt.strip()]
    parts.append("\n\n--- PATIENT CONTEXT ---\n" + (bundle_text or "(no context selected)"))

    if prior_messages:
        rendered = []
        for m in prior_messages:
            if m.role not in ("user", "assistant"):
                continue
            if not m.content:
                continue
            label = "User" if m.role == "user" else "Assistant"
            rendered.append(f"{label}: {m.content}")
        if rendered:
            parts.append("\n\n--- CONVERSATION ---\n" + "\n\n".join(rendered))

    parts.append(f"\n\nUser: {new_user_message}")
    parts.append("\nAssistant:")
    return "".join(parts)


def manifest_digest(manifest: dict[str, Any]) -> str:
    """Stable hash of the manifest for tamper-evidence in the audit log.

    The manifest is PHI-free, so the hash is safe to publish in
    audit-log payloads. Sorting by key keeps the digest stable across
    Python dict iteration orders.
    """
    payload = json.dumps(manifest, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()
