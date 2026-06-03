# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-context chat routes.

Covers the conversation-lifecycle surface
(``POST/GET/PATCH/DELETE /api/chat/conversations`` + ``GET ...`` list)
and the streaming-message endpoint (``POST .../messages``).

The whole router is gated by ``settings.enable_patient_chat``. When the
flag is off, the router is not mounted at all (see ``app.main``) and
every chat URL falls through to the global 404 handler.

Authorization model: a user can act on a conversation iff they have a
grant on the conversation's patient via the ``patient_clinicians``
access table (the ``has_patient_access`` SQL function). This is the
same patient-scoped model that gates notes, sessions, and appointments
after PR #170. ``owner_user_id`` on the conversation row is preserved
as actor data ("who started this chat") but is *not* the access proxy
— co-treating, covering, and successor clinicians inherit chat
continuity for any patient they have a grant on.

Denied reads return 404 (not 403) to avoid leaking conversation
existence to unauthorized callers — matches the
``/api/notes/{id}`` IDOR fix from PR #170.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from ..api_errors import BadRequestError, NotFoundError
from ..auth.service import get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    ChatConversationDetailResponse,
    ChatConversationListResponse,
    ChatConversationResponse,
    CreateChatConversationRequest,
    NoteResponse,
    PreviewChatContextRequest,
    PreviewChatContextResponse,
    SendChatMessageRequest,
    UpdateChatConversationRequest,
    User,
)
from ..models.audit import ResourceType
from ..repositories import (
    ChatRepository,
    LlmUsageRepository,
    NotesRepository,
    PatientDocumentRepository,
    PatientRepository,
)
from ..repositories import (
    get_chat_repository as _chat_repo_factory,
)
from ..repositories import (
    get_llm_usage_repository as _llm_usage_repo_factory,
)
from ..repositories import (
    get_notes_repository as _notes_repo_factory,
)
from ..repositories import (
    get_patient_document_repository as _patient_document_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..services import (
    AuditService,
    ChatConversationNotFoundError,
    ChatService,
    LlmUsageMeter,
    NoteService,
    get_audit_service,
)
from ..services.chat_context_bundler import (
    ContextOverflowError,
    InvalidSelectionError,
    assemble_context_bundle,
    default_source_selection,
)
from ..services.chat_llm_gateway import ChatLLMGateway, GeminiChatLLMGateway
from ..services.chat_model_resolver import (
    ChatModelResolver,
    get_chat_model_resolver,
)
from ..services.chat_turn_service import (
    ChatTurnService,
    TurnConcurrencyError,
    TurnContext,
)
from ..settings import Settings, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from ..models import ChatConversation

logger = logging.getLogger(__name__)

# Router-level ``get_tenant_context`` dependency: every chat route reads
# or writes a tenant-scoped table (``patients``, ``patient_clinicians``,
# ``chat_conversations``, ``chat_messages``, ``notes``), all of which
# carry RLS policies gated on the ``app.current_user_id`` GUC. Without
# this dependency, every chat request hits the DB with the GUC unset
# and RLS silently denies — chat only worked in prod because the
# ``pablo`` role had ``BYPASSRLS`` (the very defense-in-depth gap
# we're closing). Adding it here threads the GUC-setting side effect
# through every chat route without each handler having to remember.
router = APIRouter(
    prefix="/api/chat",
    tags=["chat"],
    dependencies=[Depends(get_tenant_context)],
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_chat_repository_dep() -> ChatRepository:
    return _chat_repo_factory()


def get_patient_repository_dep() -> PatientRepository:
    return _patient_repo_factory()


def get_notes_repository_dep() -> NotesRepository:
    return _notes_repo_factory()


def get_patient_document_repository_dep() -> PatientDocumentRepository:
    return _patient_document_repo_factory()


def get_chat_service(
    repo: ChatRepository = Depends(get_chat_repository_dep),
) -> ChatService:
    return ChatService(repo)


def get_note_service(
    notes_repo: NotesRepository = Depends(get_notes_repository_dep),
) -> NoteService:
    return NoteService(notes_repo)


# A process-wide singleton holder for the gateway. Wrapped in a list
# to avoid a module-level ``global`` (lint-friendly singleton pattern):
# the gateway itself has no per-request state, and the underlying
# ``google.genai`` client is lazily constructed inside.
_default_gateway_holder: list[ChatLLMGateway] = []


def get_chat_llm_gateway() -> ChatLLMGateway:
    """FastAPI dependency hook for the streaming Gemini gateway.

    Tests override this with ``FakeChatLLMGateway``; downstream
    consumers *could* replace it but ordinarily don't.
    """
    if not _default_gateway_holder:
        _default_gateway_holder.append(GeminiChatLLMGateway())
    return _default_gateway_holder[0]


def get_llm_usage_repository_dep() -> LlmUsageRepository:
    return _llm_usage_repo_factory()


def get_llm_usage_meter(
    repo: LlmUsageRepository = Depends(get_llm_usage_repository_dep),
    settings: Settings = Depends(get_settings),
) -> LlmUsageMeter:
    return LlmUsageMeter(repo=repo, settings=settings)


def get_chat_turn_service(
    chat_repo: ChatRepository = Depends(get_chat_repository_dep),
    notes_repo: NotesRepository = Depends(get_notes_repository_dep),
    patient_documents_repo: PatientDocumentRepository = Depends(
        get_patient_document_repository_dep
    ),
    gateway: ChatLLMGateway = Depends(get_chat_llm_gateway),
    usage_meter: LlmUsageMeter = Depends(get_llm_usage_meter),
) -> ChatTurnService:
    return ChatTurnService(
        chat_repo=chat_repo,
        notes_repo=notes_repo,
        patient_documents_repo=patient_documents_repo,
        gateway=gateway,
        usage_meter=usage_meter,
    )


# ---------------------------------------------------------------------------
# Authorization helper
# ---------------------------------------------------------------------------


def _authorize_conversation(
    conversation_id: str,
    user: User,
    chat_service: ChatService,
) -> ChatConversation:
    """Return the conversation iff the user has a grant on its patient.

    Access is gated by ``has_patient_access(patient_id, user_id)`` via
    the chat service / repository — the same model that scopes notes
    and sessions. Denied accesses raise :class:`NotFoundError` (not
    :class:`ForbiddenError`) so the surface does not leak conversation
    existence to unauthorized callers; matches the IDOR-safe shape on
    ``/api/notes/{id}``.

    Note the absence of the ``patient_repo`` argument: the chat repo
    join through ``patient_clinicians`` already enforces the same
    check, so the redundant lookup served no purpose post-#170.
    """
    try:
        return chat_service.get_conversation(conversation_id, user.id)
    except ChatConversationNotFoundError as exc:
        raise NotFoundError("Conversation not found", {"conversation_id": conversation_id}) from exc


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ChatConversationResponse,
)
def create_conversation(
    request_body: CreateChatConversationRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    patient_repo: PatientRepository = Depends(get_patient_repository_dep),
    audit: AuditService = Depends(get_audit_service),
) -> ChatConversationResponse:
    """Create a new chat conversation bound to a patient.

    Returns 404 (not 403) if the user lacks access to the patient — the
    surface should not leak patient existence.
    """
    patient = patient_repo.get(request_body.patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": request_body.patient_id})

    # Resolve the system prompt server-side when the client didn't supply
    # one (or supplied an empty string). Keeps the prompt text in the
    # backend prompts module — frontend callers can pass nothing and
    # production + eval will see the same string by construction.
    caller_system_prompt = request_body.caller_system_prompt
    if not caller_system_prompt:
        from ..prompts.chat import get_chat_system_prompt

        # ``user.provider_type`` may be either a ``ProviderType`` enum
        # (Pydantic User model) or a plain str depending on which User
        # variant flowed through the dependency chain — normalize.
        raw_provider = user.provider_type
        provider_type = getattr(raw_provider, "value", raw_provider)
        caller_system_prompt = get_chat_system_prompt(provider_type)

    display_name = f"{patient.first_name} {patient.last_name}".strip()
    conv = chat_service.create_conversation(
        patient_id=patient.id,
        owner_user_id=user.id,
        caller_feature_key=request_body.caller_feature_key,
        caller_system_prompt=caller_system_prompt,
        title=request_body.title,
        default_source_selection=request_body.default_source_selection,
        patient_display_name=display_name or None,
    )

    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_CREATED,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        changes={
            "caller_feature_key": conv.caller_feature_key,
            "system_prompt_chars": len(conv.caller_system_prompt),
        },
    )
    return ChatConversationResponse.from_conversation(conv)


@router.post(
    "/conversations/preview",
    response_model=PreviewChatContextResponse,
)
def preview_context(
    request_body: PreviewChatContextRequest,
    user: User = Depends(require_baa_acceptance),
    patient_repo: PatientRepository = Depends(get_patient_repository_dep),
    notes_repo: NotesRepository = Depends(get_notes_repository_dep),
    patient_documents_repo: PatientDocumentRepository = Depends(
        get_patient_document_repository_dep
    ),
) -> PreviewChatContextResponse:
    """Return a PHI-free context manifest for a hypothetical first turn.

    Drives the §13.4 briefing card ("I'm reading …"). Runs the same
    context bundler the streaming turn would, against the proposed
    ``source_selection``, but does NOT create a conversation, call the
    LLM, persist a chat row, or audit. Omitting ``source_selection``
    falls back to the design-doc §7.4 default.

    Returns 404 (not 403) if the user lacks access to the patient —
    matches the create_conversation surface so this route doesn't
    leak patient existence.
    """
    patient = patient_repo.get(request_body.patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": request_body.patient_id})

    selection = request_body.source_selection
    if selection is None:
        selection = default_source_selection()

    try:
        bundle = assemble_context_bundle(
            notes_repo=notes_repo,
            patient_documents_repo=patient_documents_repo,
            patient_id=patient.id,
            user_id=user.id,
            selection=selection,
        )
    except InvalidSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_selection", "message": str(exc)},
        ) from exc
    except ContextOverflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "context_too_large", "message": str(exc)},
        ) from exc

    return PreviewChatContextResponse(manifest=bundle.manifest)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ChatConversationDetailResponse,
)
def get_conversation(
    conversation_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
) -> ChatConversationDetailResponse:
    """Return a conversation with its messages in ``sequence`` order."""
    conv = _authorize_conversation(conversation_id, user, chat_service)
    messages = chat_service.list_messages(conv.id, user.id)
    # The detail read returns full message bodies, so it is a PHI-read on par
    # with get_session / get_patient / document downloads — audit it. The
    # conversation-list endpoint audits too (see list_conversations): its items
    # carry the title, which can name the patient.
    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_VIEWED,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
    )
    return ChatConversationDetailResponse.from_conversation_with_messages(conv, messages)


@router.get(
    "/conversations",
    response_model=ChatConversationListResponse,
)
def list_conversations(
    http_request: Request,
    patient_id: str = Query(...),
    caller_feature_key: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    patient_repo: PatientRepository = Depends(get_patient_repository_dep),
    audit: AuditService = Depends(get_audit_service),
) -> ChatConversationListResponse:
    """List conversations for a patient the caller has access to.

    Returns ``([], 0)`` on access denial — same shape as "no
    conversations yet" so callers can't distinguish absent from
    forbidden. The ``patient_repo.get`` lookup happens first to give a
    clean 404 on a missing / forbidden patient (matches the rest of
    the API); the repository then re-checks the grant on its own as
    defense-in-depth.
    """
    patient = patient_repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    rows, total = chat_service.list_conversations(
        patient_id=patient.id,
        user_id=user.id,
        caller_feature_key=caller_feature_key,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )
    # Each list item carries a title that defaults to "Chat about
    # {patient_display_name}" — an identifier disclosure — but NO message
    # bodies. So this is a patient-scoped index disclosure, not a per-record
    # content read (that is the conversation-detail endpoint). Audit it once,
    # scoped to the patient (resource_id = patient.id); read-coalescing collapses
    # repeated list views to one row per patient per window. Granularity matches
    # what was disclosed (titles), not bodies — see CHAT_CONVERSATION_LIST_VIEWED.
    audit.log(
        AuditAction.CHAT_CONVERSATION_LIST_VIEWED,
        user,
        http_request,
        resource_type=ResourceType.PATIENT,
        resource_id=patient.id,
        patient=patient,
    )
    return ChatConversationListResponse(
        data=[ChatConversationResponse.from_conversation(c) for c in rows],
        total=total,
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ChatConversationResponse,
)
def update_conversation(
    conversation_id: str,
    request_body: UpdateChatConversationRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
) -> ChatConversationResponse:
    """Update mutable fields. Immutable fields (patient_id, prompt, etc.)
    are not accepted by the request body and silently ignored if sent."""
    conv = _authorize_conversation(conversation_id, user, chat_service)

    was_archived = conv.archived_at is not None
    changed_fields: list[str] = []
    if request_body.title is not None:
        changed_fields.append("title")
    if request_body.default_source_selection is not None:
        changed_fields.append("default_source_selection")
    if request_body.archive is not None:
        changed_fields.append("archived_at")

    updated = chat_service.update_conversation(
        conv.id,
        user.id,
        title=request_body.title,
        default_source_selection=request_body.default_source_selection,
        archive=request_body.archive,
    )

    # ARCHIVED event fires only on the boolean transition false→true,
    # mirroring the design doc's "lifecycle events" framing.
    if request_body.archive is True and not was_archived:
        audit.log_chat_action(
            action=AuditAction.CHAT_CONVERSATION_ARCHIVED,
            user=user,
            request=http_request,
            conversation_id=updated.id,
            patient_id=updated.patient_id,
            changes={"changed_fields": changed_fields},
        )
    return ChatConversationResponse.from_conversation(updated)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_conversation(
    conversation_id: str,
    http_request: Request,
    mode: str = Query(default="purge", pattern="^(purge|archive)$"),
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """Delete a conversation. Defaults to hard-purge per design doc §6.6.

    ``mode=archive`` is the reversible soft-delete; ``mode=purge`` is
    the irreversible content deletion. Both record an audit row
    (PHI-free; no message content or manifest content).
    """
    conv = _authorize_conversation(conversation_id, user, chat_service)

    if mode == "archive":
        if conv.archived_at is None:
            chat_service.update_conversation(conv.id, user.id, archive=True)
            audit.log_chat_action(
                action=AuditAction.CHAT_CONVERSATION_ARCHIVED,
                user=user,
                request=http_request,
                conversation_id=conv.id,
                patient_id=conv.patient_id,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    deleted_message_count = chat_service.delete_conversation(conv.id, user.id)
    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_PURGED,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        changes={"message_count": deleted_message_count},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request_body: SendChatMessageRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    turn_service: ChatTurnService = Depends(get_chat_turn_service),
    resolver: ChatModelResolver = Depends(get_chat_model_resolver),
    audit: AuditService = Depends(get_audit_service),
) -> StreamingResponse:
    """Append a user turn and stream the assistant response (design doc §6.4).

    Authorization gates on :func:`has_patient_access` for the
    conversation's patient — same model as the lifecycle routes.
    Concurrent ``POST messages`` calls against the same conversation
    serialize via :class:`ChatTurnService`'s lock map and the Postgres
    row-level lock in ``next_sequence``; a request arriving while
    another is mid-stream gets a 409 immediately.

    ``requesting_user_id`` on the turn context is the *resumer's* id
    (``user.id``), not the conversation's ``owner_user_id``. PHI in
    the context bundle therefore travels with the patient — a covering
    clinician resuming a chat sees the chart through their own grants,
    not the original owner's.
    """
    conv = _authorize_conversation(conversation_id, user, chat_service)
    if conv.archived_at is not None:
        raise HTTPException(status_code=409, detail="Conversation is archived")

    model = resolver(
        user=user,
        feature_key=conv.caller_feature_key,
        override=request_body.model,
    )

    selection = request_body.source_selection
    if selection is None and conv.default_source_selection is not None:
        selection = conv.default_source_selection

    context = TurnContext(
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        requesting_user_id=user.id,
        caller_system_prompt=conv.caller_system_prompt,
        caller_feature_key=conv.caller_feature_key,
        user_message=request_body.content,
        source_selection=selection,
        model=model,
    )

    # Eagerly drain the turn while the request-scoped DB session is
    # still alive (search_path correct, transaction not committed). The
    # collected events then replay as SSE. This trades real-time delta
    # streaming for correctness — BaseHTTPMiddleware returns from
    # call_next once headers ship and closes the session in its finally
    # block, so any DB write that happens during body iteration races
    # with the connection-pool's stale search_path and can FK-violate.
    # Restore true streaming under a redesigned session lifecycle
    # (deferred close for StreamingResponse) as a follow-up.
    collected: list = []
    block_audit_fired = False
    try:
        async for event in turn_service.run_turn(context):
            collected.append(event)
            if event.kind == "error" and not block_audit_fired:
                code = event.data.get("error", "llm_error")
                if code in {"safety_block", "context_too_large", "quota_exceeded"}:
                    try:
                        audit.log_chat_action(
                            action=AuditAction.CHAT_TURN_BLOCKED,
                            user=user,
                            request=http_request,
                            conversation_id=conv.id,
                            patient_id=conv.patient_id,
                            changes={"block_reason": code},
                        )
                    except Exception:
                        logger.exception("Failed to write CHAT_TURN_BLOCKED audit row")
                    block_audit_fired = True
    except TurnConcurrencyError as exc:
        # ChatTurnService.run_turn raises before yielding the first
        # event when another turn is in flight for the same conversation.
        raise HTTPException(
            status_code=409,
            detail="Another turn is already in progress for this conversation.",
        ) from exc

    async def _sse() -> AsyncGenerator[bytes, None]:  # type: ignore[name-defined]
        for event in collected:
            payload = json.dumps(event.data, default=str)
            yield f"event: {event.kind}\ndata: {payload}\n\n".encode()

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_sse(), media_type="text/event-stream", headers=headers)


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/save-as-note",
    status_code=status.HTTP_201_CREATED,
    response_model=NoteResponse,
)
def save_message_as_note(
    conversation_id: str,
    message_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    note_service: NoteService = Depends(get_note_service),
    audit: AuditService = Depends(get_audit_service),
) -> NoteResponse:
    """Persist a single assistant chat turn as a standalone narrative note.

    THERAPY-rg5w. Chat is transient until a clinician chooses to promote a
    turn to the chart. Only assistant messages may be promoted: a user
    message has no clinical artifact value on its own. The new note is a
    ``narrative`` type with the message text in ``content.note.body``;
    source attribution (chat conversation id, message id, citation
    manifest, model) lives in a ``__source`` block on the same content
    dict so an audit reviewer can trace the note back to its chat origin.

    Authorization: the conversation lookup gates on ``has_patient_access``
    (same as every chat route), and the note write goes through the
    standard ``NotesRepository.add`` path — no chat-side bypass.
    """
    conv = _authorize_conversation(conversation_id, user, chat_service)

    messages = chat_service.list_messages(conv.id, user.id)
    msg = next((m for m in messages if m.id == message_id), None)
    if msg is None:
        raise NotFoundError(
            "Message not found",
            {"conversation_id": conv.id, "message_id": message_id},
        )
    if msg.role != "assistant":
        raise BadRequestError(
            "Only assistant messages may be saved as notes",
            {"message_id": message_id, "role": msg.role},
            code="NOT_ASSISTANT_MESSAGE",
        )
    body = (msg.content or "").strip()
    if not body:
        raise BadRequestError(
            "Cannot save an empty assistant message",
            {"message_id": message_id},
            code="EMPTY_MESSAGE",
        )

    content: dict[str, object] = {
        "note": {"body": msg.content},
        "__source": {
            "kind": "chat",
            "chat_conversation_id": conv.id,
            "chat_message_id": msg.id,
            "caller_feature_key": conv.caller_feature_key,
            # Preserve the citation manifest (note ids the assistant
            # could see) so reviewers can verify what the chat turn was
            # grounded in. This is PHI-adjacent but lives in note
            # content, not the audit log.
            "context_manifest": msg.context_manifest,
            "llm_model": msg.llm_model,
        },
    }

    note = note_service.create_standalone_note(
        patient_id=conv.patient_id,
        note_type="narrative",
        content=content,
        user_id=user.id,
    )

    # Two-surface audit: the note-creation half mirrors every other
    # standalone-note write; the chat-side half uses the existing
    # CHAT_CHART_PROMOTION action so dashboards that track chat-to-chart
    # promotions don't need a new event.
    audit.log_note_action(
        action=AuditAction.SESSION_CREATED,
        user=user,
        request=http_request,
        note_id=note.id,
        patient_id=note.patient_id,
        session_id=None,
        changes={
            "note_type": note.note_type,
            "standalone": True,
            "source": "chat",
        },
    )
    audit.log_chat_action(
        action=AuditAction.CHAT_CHART_PROMOTION,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        changes={"note_id": note.id, "message_id": msg.id},
    )
    return NoteResponse.from_note(note)


__all__ = [
    "get_chat_llm_gateway",
    "get_chat_repository_dep",
    "get_chat_service",
    "get_chat_turn_service",
    "get_llm_usage_meter",
    "get_llm_usage_repository_dep",
    "get_note_service",
    "get_notes_repository_dep",
    "get_patient_document_repository_dep",
    "get_patient_repository_dep",
    "router",
]
