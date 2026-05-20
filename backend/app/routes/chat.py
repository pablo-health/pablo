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

from ..api_errors import NotFoundError
from ..auth.service import require_baa_acceptance
from ..models import (
    AuditAction,
    ChatConversationDetailResponse,
    ChatConversationListResponse,
    ChatConversationResponse,
    CreateChatConversationRequest,
    PreviewChatContextRequest,
    PreviewChatContextResponse,
    SendChatMessageRequest,
    UpdateChatConversationRequest,
    User,
)
from ..repositories import (
    ChatRepository,
    LlmUsageRepository,
    NotesRepository,
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
    get_patient_repository as _patient_repo_factory,
)
from ..services import (
    AuditService,
    ChatConversationNotFoundError,
    ChatService,
    LlmUsageMeter,
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

router = APIRouter(prefix="/api/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_chat_repository_dep() -> ChatRepository:
    return _chat_repo_factory()


def get_patient_repository_dep() -> PatientRepository:
    return _patient_repo_factory()


def get_notes_repository_dep() -> NotesRepository:
    return _notes_repo_factory()


def get_chat_service(
    repo: ChatRepository = Depends(get_chat_repository_dep),
) -> ChatService:
    return ChatService(repo)


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
    gateway: ChatLLMGateway = Depends(get_chat_llm_gateway),
    usage_meter: LlmUsageMeter = Depends(get_llm_usage_meter),
) -> ChatTurnService:
    return ChatTurnService(
        chat_repo=chat_repo,
        notes_repo=notes_repo,
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

    display_name = f"{patient.first_name} {patient.last_name}".strip()
    conv = chat_service.create_conversation(
        patient_id=patient.id,
        owner_user_id=user.id,
        caller_feature_key=request_body.caller_feature_key,
        caller_system_prompt=request_body.caller_system_prompt,
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
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patient_repo: PatientRepository = Depends(get_patient_repository_dep),
    notes_repo: NotesRepository = Depends(get_notes_repository_dep),
    audit: AuditService = Depends(get_audit_service),
) -> PreviewChatContextResponse:
    """Return a PHI-free context manifest for a hypothetical first turn.

    Drives the §13.4 briefing card ("I'm reading …"). Runs the same
    context bundler the streaming turn would, against the proposed
    ``source_selection``, but does NOT create a conversation, call the
    LLM, or persist a chat row. The patient + notes read is still PHI
    access and lands in the audit log per § 164.312(b).

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
            patient_id=patient.id,
            user_id=user.id,
            selection=selection,
        )
    except InvalidSelectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "invalid_selection", "message": str(exc)},
        ) from exc
    except ContextOverflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "context_too_large", "message": str(exc)},
        ) from exc

    audit.log_chat_action(
        action=AuditAction.CHAT_CONTEXT_PREVIEWED,
        user=user,
        request=http_request,
        conversation_id="preview",
        patient_id=patient.id,
    )
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
    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_VIEWED,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        changes={"message_count": len(messages)},
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
    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_LISTED,
        user=user,
        request=http_request,
        conversation_id="list",
        patient_id=patient.id,
        changes={"conversation_count": total},
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

    try:
        event_iter = turn_service.run_turn(context)
    except TurnConcurrencyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Another turn is already in progress for this conversation.",
        ) from exc

    block_audit_fired = {"done": False}

    async def _sse() -> AsyncGenerator[bytes, None]:  # type: ignore[name-defined]
        try:
            async for event in event_iter:
                if event.kind == "error" and not block_audit_fired["done"]:
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
                        block_audit_fired["done"] = True
                payload = json.dumps(event.data, default=str)
                yield f"event: {event.kind}\ndata: {payload}\n\n".encode()
        except TurnConcurrencyError:
            payload = json.dumps(
                {"error": "concurrent_turn", "message": "Another turn is in flight."}
            )
            yield f"event: error\ndata: {payload}\n\n".encode()

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(_sse(), media_type="text/event-stream", headers=headers)


__all__ = [
    "get_chat_llm_gateway",
    "get_chat_repository_dep",
    "get_chat_service",
    "get_chat_turn_service",
    "get_llm_usage_meter",
    "get_llm_usage_repository_dep",
    "get_notes_repository_dep",
    "get_patient_repository_dep",
    "router",
]
