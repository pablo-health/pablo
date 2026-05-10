# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient-context chat primitive — HTTP surface.

Endpoints in this module are gated by the ``ENABLE_PATIENT_CHAT``
feature flag at router-mount time. They do not assume any specific
clinical workflow — the caller is expected to supply a system prompt
and a source-selection default at conversation creation time.

Authorization model:

* Every endpoint resolves the conversation, then enforces
  ``conversation.owner_user_id == current_user.id``. Patient-access is
  re-validated through the existing ``PatientRepository`` so the
  conversation can never out-live the patient ACL.
* The patient binding is immutable — callers that want a different
  patient open a new conversation.
* Every endpoint emits an audit event before returning. PHI never
  enters the audit payload — only counts, ids, and the manifest digest.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse

if TYPE_CHECKING:
    from collections.abc import Iterable

from ..api_errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    ChatConversationDetail,
    ChatConversationListResponse,
    ChatConversationResponse,
    ChatMessageResponse,
    CreateChatConversationRequest,
    SendChatMessageRequest,
    UpdateChatConversationRequest,
    User,
)
from ..repositories import (
    ChatRepository,
    NotesRepository,
    PatientRepository,
)
from ..repositories import (
    get_chat_repository as _chat_repo_factory,
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
    ChatPatientGoneError,
    ChatPatientNotFoundError,
    ChatPermissionError,
    ChatService,
    ContextOverflowError,
    LlmStreamGateway,
    LlmUsageMeter,
    QuotaStatus,
    build_prompt_envelope,
    estimate_tokens,
    get_audit_service,
    get_default_llm_gateway,
    get_llm_usage_meter,
    manifest_digest,
)
from ..settings import get_settings
from ..utcnow import utc_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Mirrors the DB CHECK constraint and Pydantic max on the
# ``chat_messages.content`` column — duplicated here so the route can
# fail fast before any DB write happens.
_MAX_MESSAGE_CONTENT_CHARS = 32_768


# --------------------------------------------------------------------------- #
# Dependency wiring
# --------------------------------------------------------------------------- #


def get_chat_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> ChatRepository:
    return _chat_repo_factory()


def get_notes_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> NotesRepository:
    return _notes_repo_factory()


def get_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    return _patient_repo_factory()


def get_chat_service(
    chat_repo: ChatRepository = Depends(get_chat_repository),
    notes_repo: NotesRepository = Depends(get_notes_repository),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> ChatService:
    return ChatService(
        chat_repo=chat_repo,
        notes_repo=notes_repo,
        patient_repo=patient_repo,
    )


def _resolve_model_for_feature(feature_key: str) -> str:
    settings = get_settings()
    # Justification-style features want the higher-tier model; chart-Q&A
    # falls back to the cost-optimized model when one is configured.
    if feature_key in {"rx_justification_workspace", "soap_generation"}:
        return settings.ai_model
    return settings.ai_model_flash or settings.ai_model


def _llm_gateway_for(feature_key: str) -> LlmStreamGateway:
    return get_default_llm_gateway(_resolve_model_for_feature(feature_key))


def _tenant_id_for(ctx: TenantContext) -> str:
    return ctx.practice_id or "default"


# --------------------------------------------------------------------------- #
# Conversation CRUD
# --------------------------------------------------------------------------- #


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=ChatConversationResponse,
)
def create_conversation(
    body: CreateChatConversationRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
) -> ChatConversationResponse:
    """Create a new patient-bound chat conversation."""
    settings = get_settings()
    if len(body.caller_system_prompt) > settings.chat_max_system_prompt_chars:
        raise BadRequestError(
            "caller_system_prompt exceeds maximum allowed length",
            {"max_chars": settings.chat_max_system_prompt_chars},
            code="SYSTEM_PROMPT_TOO_LONG",
        )

    try:
        conv = chat_service.create_conversation(
            owner_user_id=user.id,
            patient_id=body.patient_id,
            caller_feature_key=body.caller_feature_key,
            caller_system_prompt=body.caller_system_prompt,
            title=body.title,
            default_source_selection=body.default_source_selection,
        )
    except ChatPatientNotFoundError as exc:
        raise NotFoundError("Patient not found", {"patient_id": body.patient_id}) from exc
    except ValueError as exc:
        raise BadRequestError(str(exc), {}, code="INVALID_REQUEST") from exc

    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_CREATED,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        changes={"caller_feature_key": conv.caller_feature_key},
    )
    return ChatConversationResponse.from_conversation(conv)


@router.get(
    "/conversations",
    response_model=ChatConversationListResponse,
)
def list_conversations(
    http_request: Request,
    patient_id: str = Query(..., description="Required — patient to list conversations for"),
    caller_feature_key: str | None = Query(default=None),
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
    patient_repo: PatientRepository = Depends(get_patient_repository),
) -> ChatConversationListResponse:
    """List a user's conversations for a given patient."""
    if patient_repo.get(patient_id, user.id) is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})
    rows, total = chat_service.list_conversations(
        patient_id=patient_id,
        owner_user_id=user.id,
        caller_feature_key=caller_feature_key,
        include_archived=include_archived,
        limit=limit,
        offset=offset,
    )
    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_LISTED,
        user=user,
        request=http_request,
        conversation_id="list",
        patient_id=patient_id,
        changes={"returned_count": len(rows), "total": total},
    )
    return ChatConversationListResponse(
        data=[ChatConversationResponse.from_conversation(c) for c in rows],
        total=total,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ChatConversationDetail,
)
def get_conversation(
    conversation_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
) -> ChatConversationDetail:
    try:
        conv = chat_service.get_conversation(conversation_id, requesting_user_id=user.id)
    except ChatConversationNotFoundError as exc:
        raise NotFoundError(
            "Conversation not found", {"conversation_id": conversation_id}
        ) from exc
    except ChatPermissionError as exc:
        raise ForbiddenError(
            "Conversation not accessible",
            {"conversation_id": conversation_id},
        ) from exc

    messages = chat_service.list_messages(conversation_id)
    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_VIEWED,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        changes={"message_count": len(messages)},
    )
    return ChatConversationDetail(
        id=conv.id,
        patient_id=conv.patient_id,
        owner_user_id=conv.owner_user_id,
        title=conv.title,
        caller_feature_key=conv.caller_feature_key,
        default_source_selection=conv.default_source_selection,
        created_at=conv.created_at,
        last_turn_at=conv.last_turn_at,
        archived_at=conv.archived_at,
        messages=[ChatMessageResponse.from_message(m) for m in messages],
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ChatConversationResponse,
)
def update_conversation(
    conversation_id: str,
    body: UpdateChatConversationRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
) -> ChatConversationResponse:
    try:
        conv = chat_service.update_conversation(
            conversation_id,
            requesting_user_id=user.id,
            title=body.title,
            default_source_selection=body.default_source_selection,
            archive=body.archive,
        )
    except ChatConversationNotFoundError as exc:
        raise NotFoundError(
            "Conversation not found", {"conversation_id": conversation_id}
        ) from exc
    except ChatPermissionError as exc:
        raise ForbiddenError(
            "Conversation not accessible",
            {"conversation_id": conversation_id},
        ) from exc

    if body.archive is True:
        audit.log_chat_action(
            action=AuditAction.CHAT_CONVERSATION_ARCHIVED,
            user=user,
            request=http_request,
            conversation_id=conv.id,
            patient_id=conv.patient_id,
        )
    else:
        changed: list[str] = []
        if body.title is not None:
            changed.append("title")
        if body.default_source_selection is not None:
            changed.append("default_source_selection")
        if body.archive is False:
            changed.append("unarchive")
        audit.log_chat_action(
            action=AuditAction.CHAT_CONVERSATION_UPDATED,
            user=user,
            request=http_request,
            conversation_id=conv.id,
            patient_id=conv.patient_id,
            changes={"changed_fields": changed},
        )
    return ChatConversationResponse.from_conversation(conv)


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
    """Hard-purge by default; ``mode=archive`` soft-deletes only.

    Conversations are clinician working memory (per the design doc),
    not the designated record set, so the clinician owns the right to
    purge them on demand. The audit record of the purge survives the
    purge — only the *content* is destroyed.
    """
    if mode == "archive":
        try:
            conv = chat_service.update_conversation(
                conversation_id,
                requesting_user_id=user.id,
                archive=True,
            )
        except ChatConversationNotFoundError as exc:
            raise NotFoundError(
                "Conversation not found", {"conversation_id": conversation_id}
            ) from exc
        except ChatPermissionError as exc:
            raise ForbiddenError(
                "Conversation not accessible",
                {"conversation_id": conversation_id},
            ) from exc
        audit.log_chat_action(
            action=AuditAction.CHAT_CONVERSATION_ARCHIVED,
            user=user,
            request=http_request,
            conversation_id=conv.id,
            patient_id=conv.patient_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    try:
        conv, message_count = chat_service.purge_conversation(
            conversation_id, requesting_user_id=user.id
        )
    except ChatConversationNotFoundError as exc:
        raise NotFoundError(
            "Conversation not found", {"conversation_id": conversation_id}
        ) from exc
    except ChatPermissionError as exc:
        raise ForbiddenError(
            "Conversation not accessible",
            {"conversation_id": conversation_id},
        ) from exc

    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_PURGED,
        user=user,
        request=http_request,
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        changes={"message_count": message_count},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/conversations",
    status_code=status.HTTP_204_NO_CONTENT,
)
def bulk_purge_for_patient(
    http_request: Request,
    patient_id: str = Query(..., description="Required — purge all owned chats for this patient"),
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    purged = chat_service.purge_owner_conversations_for_patient(
        patient_id=patient_id, owner_user_id=user.id
    )
    audit.log_chat_action(
        action=AuditAction.CHAT_CONVERSATION_PURGED,
        user=user,
        request=http_request,
        conversation_id="bulk",
        patient_id=patient_id,
        changes={"conversation_count": purged},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Streaming turn endpoint
# --------------------------------------------------------------------------- #


def _sse_event(name: str, payload: dict[str, Any]) -> bytes:
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n".encode()


@router.post(
    "/conversations/{conversation_id}/messages",
)
def send_message(  # noqa: PLR0915 — SSE handler intentionally inlines stream lifecycle
    conversation_id: str,
    body: SendChatMessageRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    chat_service: ChatService = Depends(get_chat_service),
    ctx: TenantContext = Depends(get_tenant_context),
    audit: AuditService = Depends(get_audit_service),
    meter: LlmUsageMeter = Depends(get_llm_usage_meter),
) -> StreamingResponse:
    """Append a user turn and stream the assistant response over SSE."""
    settings = get_settings()
    if len(body.content) > _MAX_MESSAGE_CONTENT_CHARS:
        raise BadRequestError(
            "Message content exceeds maximum allowed length",
            {"max_chars": _MAX_MESSAGE_CONTENT_CHARS},
            code="MESSAGE_TOO_LONG",
        )

    if body.source_selection and "pasted_text" in body.source_selection:
        pasted = body.source_selection.get("pasted_text") or {}
        if isinstance(pasted, dict):
            pasted_content = pasted.get("content") or ""
            if len(pasted_content) > settings.chat_max_pasted_chars:
                raise BadRequestError(
                    "Pasted content exceeds maximum allowed length",
                    {"max_chars": settings.chat_max_pasted_chars},
                    code="PASTED_TEXT_TOO_LONG",
                )

    try:
        conv = chat_service.get_conversation(conversation_id, requesting_user_id=user.id)
    except ChatConversationNotFoundError as exc:
        raise NotFoundError(
            "Conversation not found", {"conversation_id": conversation_id}
        ) from exc
    except ChatPermissionError as exc:
        raise ForbiddenError(
            "Conversation not accessible",
            {"conversation_id": conversation_id},
        ) from exc

    if conv.archived_at is not None:
        raise ConflictError(
            "Conversation is archived",
            {"conversation_id": conversation_id},
            code="CONVERSATION_ARCHIVED",
        )

    quota = meter.check_quota(
        tenant_id=_tenant_id_for(ctx),
        user_id=user.id,
        feature_key=conv.caller_feature_key,
    )
    if quota.status == QuotaStatus.HARD_BLOCK:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": {
                    "code": "QUOTA_EXCEEDED",
                    "message": "Monthly LLM usage quota exceeded for this feature",
                    "details": {
                        "feature_key": quota.feature_key,
                        "limit": quota.limit,
                        "used": quota.used,
                    },
                }
            },
        )

    try:
        conv, user_msg, assistant_msg, bundle = chat_service.begin_turn(
            conversation_id=conversation_id,
            requesting_user_id=user.id,
            content=body.content,
            source_selection=body.source_selection,
        )
    except ChatPatientGoneError as exc:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={
                "error": {
                    "code": "PATIENT_GONE",
                    "message": "The patient bound to this conversation no longer exists",
                    "details": {"conversation_id": conversation_id},
                }
            },
        ) from exc
    except ContextOverflowError as exc:
        # The user-message row was never persisted — bundle assembly happens
        # before any writes inside begin_turn — so we can fail cleanly.
        raise BadRequestError(
            str(exc),
            {"conversation_id": conversation_id},
            code="CONTEXT_OVERFLOW",
        ) from exc

    # Build the prompt envelope from prior turns (excluding the new user
    # turn and the empty assistant placeholder we just inserted).
    all_messages = chat_service.list_messages(conversation_id)
    prior = [
        m for m in all_messages
        if m.id not in {user_msg.id, assistant_msg.id} and m.role in {"user", "assistant"}
    ]
    prompt = build_prompt_envelope(
        caller_system_prompt=conv.caller_system_prompt,
        bundle_text=bundle.text,
        prior_messages=prior,
        new_user_message=body.content,
    )
    input_tokens = estimate_tokens(prompt)
    model_id = _resolve_model_for_feature(conv.caller_feature_key)
    gateway = _llm_gateway_for(conv.caller_feature_key)

    digest = manifest_digest(bundle.manifest())
    started_at = utc_now()

    def _event_stream() -> Iterable[bytes]:
        meta_payload: dict[str, Any] = {
            "user_message_id": user_msg.id,
            "assistant_message_id": assistant_msg.id,
            "input_tokens": input_tokens,
            "model": model_id,
        }
        if bundle.sources_dropped:
            meta_payload["sources_dropped"] = bundle.sources_dropped
        if quota.status == QuotaStatus.SOFT_WARN:
            meta_payload["quota_remaining_pct"] = quota.quota_remaining_pct
        yield _sse_event("meta", meta_payload)

        full_text = ""
        try:
            for chunk in gateway.stream(prompt=prompt, model=model_id):
                if not chunk.text:
                    continue
                full_text += chunk.text
                yield _sse_event("delta", {"text": chunk.text})
            result = gateway.finish()
        except Exception as exc:
            logger.exception("chat: LLM stream failed")
            chat_service.finalize_turn(
                assistant_msg.id,
                content=full_text,
                input_tokens=input_tokens,
                output_tokens=estimate_tokens(full_text),
                llm_model=model_id,
                llm_finish_reason="error",
                llm_error=type(exc).__name__,
            )
            audit.log_chat_action(
                action=AuditAction.CHAT_TURN_FAILED,
                user=user,
                request=http_request,
                conversation_id=conv.id,
                patient_id=conv.patient_id,
                changes={
                    "input_tokens": input_tokens,
                    "output_tokens": estimate_tokens(full_text),
                    "llm_model": model_id,
                    "llm_error": type(exc).__name__,
                    "context_manifest_digest": digest,
                },
            )
            yield _sse_event(
                "error",
                {"error": "llm_error", "message": "LLM call failed"},
            )
            return

        finish_reason = result.finish_reason
        # Buffered gateway emits the whole text in one chunk; if the
        # streaming loop didn't get any content we fall back to the
        # gateway's final payload.
        if not full_text and result.content:
            full_text = result.content
            yield _sse_event("delta", {"text": full_text})

        chat_service.finalize_turn(
            assistant_msg.id,
            content=full_text,
            input_tokens=input_tokens,
            output_tokens=result.output_tokens,
            llm_model=model_id,
            llm_finish_reason=finish_reason,
            llm_error=result.error,
        )

        meter.record_turn(
            tenant_id=_tenant_id_for(ctx),
            user_id=user.id,
            feature_key=conv.caller_feature_key,
            model=model_id,
            input_tokens=input_tokens,
            output_tokens=result.output_tokens,
        )

        if finish_reason == "safety":
            audit.log_chat_action(
                action=AuditAction.CHAT_TURN_FAILED,
                user=user,
                request=http_request,
                conversation_id=conv.id,
                patient_id=conv.patient_id,
                changes={
                    "input_tokens": input_tokens,
                    "output_tokens": result.output_tokens,
                    "llm_model": model_id,
                    "llm_error": "SafetyBlockError",
                    "context_manifest_digest": digest,
                },
            )
            yield _sse_event(
                "error",
                {"error": "safety_block", "message": "Output blocked by safety filter"},
            )
            return

        duration_ms = int((utc_now() - started_at).total_seconds() * 1000)
        audit.log_chat_action(
            action=AuditAction.CHAT_TURN_COMPLETED,
            user=user,
            request=http_request,
            conversation_id=conv.id,
            patient_id=conv.patient_id,
            changes={
                "user_message_id": user_msg.id,
                "assistant_message_id": assistant_msg.id,
                "caller_feature_key": conv.caller_feature_key,
                "input_tokens": input_tokens,
                "output_tokens": result.output_tokens,
                "llm_model": model_id,
                "context_manifest_digest": digest,
                "duration_ms": duration_ms,
            },
        )
        yield _sse_event(
            "done",
            {
                "output_tokens": result.output_tokens,
                "finish_reason": finish_reason,
            },
        )

    return StreamingResponse(_event_stream(), media_type="text/event-stream")
