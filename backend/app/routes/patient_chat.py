# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Chat where the patient is the one asking.

The clinician surface in :mod:`chat` is a chart-grounded assistant for the
person who wrote the chart. This one is for the person the chart is about,
between visits, and three things follow from that:

* **The patient id comes from the principal, never from the request.**
  There is no ``patient_id`` anywhere in these routes' inputs. The
  conversation id a patient names is looked up together with their own id
  and ``owner_user_id IS NULL``, so a clinician's conversation *about*
  them, or another patient's, is a 404 with nothing to distinguish it
  from a typo. Row-level security tests the same pair underneath.
* **No chart, ever.** Every turn runs with ``ground_in_chart=False``, so
  no note, document or medication is opened for it. The system prompt is
  the surface's own, resolved server-side, and a client cannot supply one.
* **Every turn is audited.** On the clinician surface a turn is the
  expected case and lifecycle events are enough. Here the actor is the
  subject and there is no clinician in the room, so how often and when
  this person talked to it is itself the reviewable fact. Ids only, never
  content.

Mounted behind the same ``settings.enable_patient_chat`` flag as the
clinician router; the flag off means every URL here is a 404.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from ..api_errors import ForbiddenError, NotFoundError
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..models import (
    AuditAction,
    CreatePatientChatConversationRequest,
    PatientChatConversationDetailResponse,
    PatientChatConversationListResponse,
    PatientChatConversationResponse,
    SendPatientChatMessageRequest,
    UpdatePatientChatConversationRequest,
)
from ..models.audit import ResourceType
from ..rate_limit import get_chat_send_limiter
from ..services import AuditService, ChatConversationNotFoundError, ChatService, get_audit_service
from ..services.chat_model_resolver import ChatModelResolver, get_chat_model_resolver
from ..services.chat_turn_service import ChatTurnService, TurnConcurrencyError, TurnContext
from .chat import (
    get_chat_llm_gateway,
    get_chat_repository_dep,
    get_chat_service,
    get_medication_repository_dep,
    get_notes_repository_dep,
    get_patient_document_repository_dep,
    sse_response,
)

if TYPE_CHECKING:
    from ..medications.repository import MedicationRepository
    from ..models import ChatConversation
    from ..repositories import ChatRepository, NotesRepository, PatientDocumentRepository
    from ..services.chat_llm_gateway import ChatLLMGateway
    from ..services.chat_turn_service import TurnStreamEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patient/chat", tags=["patient-chat"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

# Error codes on the turn stream that the clinician surface audits as a
# blocked turn. Same set here, for the same reason.
_BLOCKED_TURN_CODES = frozenset({"safety_block", "context_too_large", "quota_exceeded"})


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_patient_chat_turn_service(
    chat_repo: ChatRepository = Depends(get_chat_repository_dep),
    notes_repo: NotesRepository = Depends(get_notes_repository_dep),
    patient_documents_repo: PatientDocumentRepository = Depends(
        get_patient_document_repository_dep
    ),
    medication_repo: MedicationRepository = Depends(get_medication_repository_dep),
    gateway: ChatLLMGateway = Depends(get_chat_llm_gateway),
) -> ChatTurnService:
    """The turn service for a patient turn.

    Same repositories as the clinician service, none of which a patient
    turn opens (``ground_in_chart`` is always off), and no usage meter:
    the meter keys spend on a clinician user id, and a patient has none.
    Burst protection is the rate limiter on the send route; accounting a
    practice's patient-chat spend is separate work.
    """
    return ChatTurnService(
        chat_repo=chat_repo,
        notes_repo=notes_repo,
        patient_documents_repo=patient_documents_repo,
        medication_repo=medication_repo,
        gateway=gateway,
        usage_meter=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on every route here.

    A between-visit conversation is the patient's own words about how
    they are doing. A link that reached the wrong inbox is one factor in
    a stranger's hands, and unlike an appointment time the blast radius
    of reading it is not one person's schedule.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


def _own_conversation(
    conversation_id: str, patient: PatientContext, chat_service: ChatService
) -> ChatConversation:
    """The conversation iff the calling patient started it, else 404."""
    try:
        return chat_service.get_patient_conversation(conversation_id, patient.patient_id)
    except ChatConversationNotFoundError as exc:
        raise NotFoundError("Conversation not found", {"conversation_id": conversation_id}) from exc


def _turn_audit(event: TurnStreamEvent, turn_ids: dict[str, object]) -> tuple[AuditAction, dict]:
    """Which per-turn audit row an event earns, if any.

    ``done`` earns a ``CHAT_TURN`` row carrying the two message ids the
    ``meta`` event announced; a blocked error earns ``CHAT_TURN_BLOCKED``.
    Anything else earns nothing, signalled by an empty action.
    """
    if event.kind == "done":
        return AuditAction.CHAT_TURN, dict(turn_ids)
    if event.kind == "error":
        code = str(event.data.get("error", "llm_error"))
        if code in _BLOCKED_TURN_CODES:
            return AuditAction.CHAT_TURN_BLOCKED, {"block_reason": code}
    return AuditAction.CHAT_TURN, {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/conversations",
    status_code=status.HTTP_201_CREATED,
    response_model=PatientChatConversationResponse,
)
def create_conversation(
    request_body: CreatePatientChatConversationRequest,
    request: Request,
    patient: CurrentPatient,
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientChatConversationResponse:
    """Start a conversation. Exempt from the subscription gate like every
    patient route: a patient does not hold the practice's subscription."""
    _require_stepped_up(patient)
    conv = chat_service.create_patient_conversation(
        patient_id=patient.patient_id, title=request_body.title
    )
    audit.log_patient_principal_action(
        action=AuditAction.CHAT_CONVERSATION_CREATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.CHAT_CONVERSATION,
        resource_id=conv.id,
        changes={"caller_feature_key": conv.caller_feature_key},
    )
    return PatientChatConversationResponse.from_conversation(conv)


@router.get("/conversations", response_model=PatientChatConversationListResponse)
def list_conversations(
    request: Request,
    patient: CurrentPatient,
    include_archived: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientChatConversationListResponse:
    """The conversations this patient started, most recent first."""
    _require_stepped_up(patient)
    rows, total = chat_service.list_patient_conversations(
        patient_id=patient.patient_id,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )
    # Titles, no bodies: one index-disclosure row scoped to the patient,
    # coalesced with repeated views — the same granularity as the
    # clinician list.
    audit.log_patient_principal_action(
        action=AuditAction.CHAT_CONVERSATION_LIST_VIEWED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT,
        resource_id=patient.patient_id,
    )
    return PatientChatConversationListResponse(
        data=[PatientChatConversationResponse.from_conversation(c) for c in rows],
        total=total,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=PatientChatConversationDetailResponse,
)
def get_conversation(
    conversation_id: str,
    request: Request,
    patient: CurrentPatient,
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientChatConversationDetailResponse:
    """One conversation with its messages in order."""
    _require_stepped_up(patient)
    conv = _own_conversation(conversation_id, patient, chat_service)
    messages = chat_service.list_patient_messages(conv.id, patient.patient_id)
    audit.log_patient_principal_action(
        action=AuditAction.CHAT_CONVERSATION_VIEWED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.CHAT_CONVERSATION,
        resource_id=conv.id,
    )
    return PatientChatConversationDetailResponse.from_conversation_with_messages(conv, messages)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=PatientChatConversationResponse,
)
def update_conversation(
    conversation_id: str,
    request_body: UpdatePatientChatConversationRequest,
    request: Request,
    patient: CurrentPatient,
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientChatConversationResponse:
    """Rename, archive or un-archive."""
    _require_stepped_up(patient)
    conv = _own_conversation(conversation_id, patient, chat_service)
    was_archived = conv.archived_at is not None

    updated = chat_service.update_patient_conversation(
        conv.id,
        patient.patient_id,
        title=request_body.title,
        archive=request_body.archive,
    )
    if request_body.archive is True and not was_archived:
        audit.log_patient_principal_action(
            action=AuditAction.CHAT_CONVERSATION_ARCHIVED,
            request=request,
            patient_id=patient.patient_id,
            resource_type=ResourceType.CHAT_CONVERSATION,
            resource_id=updated.id,
        )
    return PatientChatConversationResponse.from_conversation(updated)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    request: Request,
    patient: CurrentPatient,
    mode: str = Query(default="purge", pattern="^(purge|archive)$"),
    chat_service: ChatService = Depends(get_chat_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> Response:
    """Delete a conversation. ``purge`` (the default) removes it and its
    messages outright; ``archive`` is the reversible form."""
    _require_stepped_up(patient)
    conv = _own_conversation(conversation_id, patient, chat_service)

    if mode == "archive":
        if conv.archived_at is None:
            chat_service.update_patient_conversation(conv.id, patient.patient_id, archive=True)
            audit.log_patient_principal_action(
                action=AuditAction.CHAT_CONVERSATION_ARCHIVED,
                request=request,
                patient_id=patient.patient_id,
                resource_type=ResourceType.CHAT_CONVERSATION,
                resource_id=conv.id,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    deleted_message_count = chat_service.delete_patient_conversation(conv.id, patient.patient_id)
    audit.log_patient_principal_action(
        action=AuditAction.CHAT_CONVERSATION_PURGED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.CHAT_CONVERSATION,
        resource_id=conv.id,
        changes={"message_count": deleted_message_count},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    request_body: SendPatientChatMessageRequest,
    request: Request,
    patient: CurrentPatient,
    chat_service: ChatService = Depends(get_chat_service),
    turn_service: ChatTurnService = Depends(get_patient_chat_turn_service),
    resolver: ChatModelResolver = Depends(get_chat_model_resolver),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> Response:
    """Send a message and stream the reply as server-sent events.

    The turn is drained inside the handler and replayed, exactly as the
    clinician route does and for the same session-lifecycle reason. A
    second turn arriving while one is in flight gets a 409.
    """
    _require_stepped_up(patient)
    get_chat_send_limiter().check(patient.patient_id)

    conv = _own_conversation(conversation_id, patient, chat_service)
    if conv.archived_at is not None:
        raise HTTPException(status_code=409, detail="Conversation is archived")

    model = resolver(user=None, feature_key=conv.caller_feature_key, override=None)
    context = TurnContext(
        conversation_id=conv.id,
        patient_id=conv.patient_id,
        requesting_user_id=patient.patient_id,
        caller_system_prompt=conv.caller_system_prompt,
        caller_feature_key=conv.caller_feature_key,
        user_message=request_body.content,
        source_selection=None,
        model=model,
        patient_principal=True,
        ground_in_chart=False,
    )

    collected: list[TurnStreamEvent] = []
    turn_ids: dict[str, object] = {}
    try:
        async for event in turn_service.run_turn(context):
            collected.append(event)
            if event.kind == "meta":
                turn_ids = {
                    "user_message_id": event.data.get("user_message_id"),
                    "assistant_message_id": event.data.get("assistant_message_id"),
                }
                continue
            action, changes = _turn_audit(event, turn_ids)
            if not changes:
                continue
            # Best-effort: an audit-write failure must not break the stream.
            try:
                audit.log_patient_principal_action(
                    action=action,
                    request=request,
                    patient_id=patient.patient_id,
                    resource_type=ResourceType.CHAT_CONVERSATION,
                    resource_id=conv.id,
                    changes=changes,
                )
            except Exception:
                logger.exception("Failed to write %s audit row", action.value)
    except TurnConcurrencyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Another turn is already in progress for this conversation.",
        ) from exc

    return sse_response(collected)


__all__ = ["get_patient_chat_turn_service", "router"]
