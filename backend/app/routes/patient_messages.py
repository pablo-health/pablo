# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Secure messaging between a patient and their practice.

Unlike email, the words live here. This is the system of record for a
patient message, which is why the routes below read the way they do.

Two surfaces, three routers:

  Patient portal — ``/api/patient/messages``

    POST   /threads                        -> start a thread with its first message
    POST   /threads/{thread_id}/messages   -> send into a thread
    GET    /threads                        -> the patient's threads + unread counts
    GET    /threads/{thread_id}            -> one thread with its messages
    POST   /threads/{thread_id}/read       -> mark what was sent to them as read

  Clinician —

    GET    /api/patients/{patient_id}/message-threads  -> a patient's threads
    GET    /api/message-threads/{thread_id}            -> one thread with its messages
    POST   /api/message-threads/{thread_id}/replies    -> reply

**The patient id comes from the principal, never from the request.** No
patient route takes one, in the path or the body, so there is nothing to
compare and nothing to forget. ``sender`` is decided the same way: the
patient routes write ``'patient'`` and the reply route writes
``'clinician'``, and neither reads a value a client sent.

**A clinician reaches these rows through ``has_patient_access``** — the
same ``patient_clinicians`` grant that scopes notes and sessions. A
clinician with no grant gets a 404 rather than a 403, so the surface never
confirms that a thread exists.

**Audit follows who is acting.** A clinician opening a thread is a
disclosure and is recorded; a patient reading their own messages is not,
and is not. Sends and thread creation are recorded on both sides, and
marking a message read is recorded because "this was delivered and opened"
is the fact a dispute turns on. Every payload carries ids and counts —
never a subject, never a body.

**After a message is stored, registered post-message callbacks run.** A
deployment may configure them; the default is none. See
:mod:`app.services.patient_message_hooks` for what a callback may assume.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from ..api_errors import NotFoundError
from ..auth.patient_context import PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AuditAction,
    MarkThreadReadResponse,
    PatientMessage,
    PatientMessageResponse,
    PatientMessageThread,
    PatientMessageThreadDetailResponse,
    PatientMessageThreadListResponse,
    PatientMessageThreadResponse,
    SendMessageRequest,
    StartThreadRequest,
    User,
)
from ..models.audit import ResourceType
from ..models.patient_message import (
    SENDER_CLINICIAN,
    SENDER_PATIENT,
    THREAD_STATUS_OPEN,
)
from ..rate_limit import get_patient_message_send_limiter
from ..repositories import PatientMessageRepository
from ..repositories import get_patient_message_repository as _repo_factory
from ..services import AuditService, get_audit_service
from ..services.patient_message_hooks import PatientMessageEvent, dispatch_patient_message
from ..utcnow import utc_now

logger = logging.getLogger(__name__)

patient_messages_router = APIRouter(prefix="/api/patient/messages", tags=["patient-messages"])
patient_threads_router = APIRouter(prefix="/api/patients", tags=["patient-messages"])
message_threads_router = APIRouter(prefix="/api/message-threads", tags=["patient-messages"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_patient_message_repository() -> PatientMessageRepository:
    """The tenant-scoped repository.

    No ``get_tenant_context`` dependency here, unlike the clinician-only
    repositories elsewhere: this one is shared by both surfaces, and a
    patient request arms its schema through ``get_patient_context``
    instead. Each route below already depends on one principal or the
    other, so the ``search_path`` is set either way by the time this runs.
    """
    return _repo_factory()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _own_thread(
    thread_id: str, patient: PatientContext, repo: PatientMessageRepository
) -> PatientMessageThread:
    """The thread iff it is the calling patient's, else 404."""
    thread = repo.get_patient_thread(thread_id, patient.patient_id)
    if thread is None:
        raise NotFoundError("Thread not found", {"thread_id": thread_id})
    return thread


def _accessible_thread(
    thread_id: str, user: User, repo: PatientMessageRepository
) -> PatientMessageThread:
    """The thread iff the clinician has a grant on its patient, else 404.

    404 rather than 403 on purpose: a clinician without a grant learns
    nothing about whether the thread exists.
    """
    thread = repo.get_thread(thread_id, user.id)
    if thread is None:
        raise NotFoundError("Thread not found", {"thread_id": thread_id})
    return thread


def _schedule_hooks(
    background_tasks: BackgroundTasks,
    *,
    practice_schema: str | None,
    thread: PatientMessageThread,
    message: PatientMessage,
) -> None:
    """Hand the stored message to whatever the deployment registered.

    Scheduled rather than called: a callback runs after the handler has
    returned, so a slow or broken one cannot delay or fail the send. The
    payload is self-contained, so a callback never reads the row back.
    """
    background_tasks.add_task(
        dispatch_patient_message,
        PatientMessageEvent(
            practice_schema=practice_schema,
            patient_id=message.patient_id,
            thread_id=message.thread_id,
            message_id=message.id,
            sender=message.sender,
            subject=thread.subject,
            body=message.body,
            created_at=message.created_at,
        ),
    )


# ---------------------------------------------------------------------------
# Patient surface
# ---------------------------------------------------------------------------


@patient_messages_router.post(
    "/threads",
    status_code=status.HTTP_201_CREATED,
    response_model=PatientMessageThreadDetailResponse,
)
def start_thread(
    request_body: StartThreadRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    patient: CurrentPatient,
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientMessageThreadDetailResponse:
    """Start a thread and send its first message.

    Exempt from the subscription gate like every patient route: a patient
    does not hold the practice's subscription, and a practice whose billing
    lapsed should not silently stop accepting its patients' messages.
    """
    get_patient_message_send_limiter().check(patient.patient_id)

    now = utc_now()
    thread = PatientMessageThread(
        id=str(uuid.uuid4()),
        patient_id=patient.patient_id,
        subject=request_body.subject,
        status=THREAD_STATUS_OPEN,
        created_at=now,
        last_message_at=now,
    )
    message = PatientMessage(
        id=str(uuid.uuid4()),
        thread_id=thread.id,
        patient_id=patient.patient_id,
        sender=SENDER_PATIENT,
        body=request_body.body,
        created_at=now,
    )
    thread, message = repo.add_patient_thread(thread, message)

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_CREATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_MESSAGE_THREAD,
        resource_id=thread.id,
        changes={"has_subject": thread.subject is not None},
    )
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_MESSAGE_SENT,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_MESSAGE_THREAD,
        resource_id=thread.id,
        changes={"message_id": message.id, "sender": SENDER_PATIENT},
    )
    _schedule_hooks(
        background_tasks,
        practice_schema=patient.practice_schema,
        thread=thread,
        message=message,
    )
    return PatientMessageThreadDetailResponse.from_thread_with_messages(thread, [message])


@patient_messages_router.post(
    "/threads/{thread_id}/messages",
    status_code=status.HTTP_201_CREATED,
    response_model=PatientMessageResponse,
)
def send_patient_message(
    thread_id: str,
    request_body: SendMessageRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    patient: CurrentPatient,
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientMessageResponse:
    """Append a message to one of the patient's own threads."""
    get_patient_message_send_limiter().check(patient.patient_id)

    thread = _own_thread(thread_id, patient, repo)
    message = repo.add_patient_message(
        PatientMessage(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            patient_id=patient.patient_id,
            sender=SENDER_PATIENT,
            body=request_body.body,
            created_at=utc_now(),
        )
    )
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_MESSAGE_SENT,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_MESSAGE_THREAD,
        resource_id=thread.id,
        changes={"message_id": message.id, "sender": SENDER_PATIENT},
    )
    _schedule_hooks(
        background_tasks,
        practice_schema=patient.practice_schema,
        thread=thread,
        message=message,
    )
    return PatientMessageResponse.from_message(message)


@patient_messages_router.get("/threads", response_model=PatientMessageThreadListResponse)
def list_patient_threads(
    patient: CurrentPatient,
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    _: None = Depends(subscription_exempt),
) -> PatientMessageThreadListResponse:
    """This patient's threads, newest activity first, with unread counts.

    Not audited. A patient reading their own record is not a disclosure —
    the settled principle behind the patient-principal audit model — and a
    row per portal visit would bury the disclosures that do matter.
    """
    rows = repo.list_patient_threads(patient.patient_id)
    return PatientMessageThreadListResponse(
        data=[PatientMessageThreadResponse.from_thread(thread, unread) for thread, unread in rows],
        total=len(rows),
    )


@patient_messages_router.get(
    "/threads/{thread_id}", response_model=PatientMessageThreadDetailResponse
)
def get_patient_thread(
    thread_id: str,
    patient: CurrentPatient,
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    _: None = Depends(subscription_exempt),
) -> PatientMessageThreadDetailResponse:
    """One of the patient's own threads with its messages. Not audited; see above."""
    thread = _own_thread(thread_id, patient, repo)
    messages = repo.list_patient_messages(thread.id, patient.patient_id)
    return PatientMessageThreadDetailResponse.from_thread_with_messages(thread, messages)


@patient_messages_router.post("/threads/{thread_id}/read", response_model=MarkThreadReadResponse)
def mark_thread_read(
    thread_id: str,
    request: Request,
    patient: CurrentPatient,
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> MarkThreadReadResponse:
    """Mark what the practice sent as read. Leaves the patient's own messages alone."""
    thread = _own_thread(thread_id, patient, repo)
    marked = repo.mark_thread_read(thread.id, patient.patient_id, utc_now())
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_READ,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_MESSAGE_THREAD,
        resource_id=thread.id,
        changes={"marked_read": marked},
    )
    return MarkThreadReadResponse(marked_read=marked)


# ---------------------------------------------------------------------------
# Clinician surface
# ---------------------------------------------------------------------------


@patient_threads_router.get(
    "/{patient_id}/message-threads", response_model=PatientMessageThreadListResponse
)
def list_threads_for_patient(
    patient_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageThreadListResponse:
    """The threads for one patient. Empty when the caller has no grant.

    Audited patient-scoped rather than per thread: the list discloses that
    this patient has correspondence and when it last moved, not what any of
    it says. Opening a thread is the content read, and it has its own row.
    """
    threads = repo.list_threads_for_patient(patient_id, user.id)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_VIEWED,
        user=user,
        request=request,
        resource_id=patient_id,
        patient_id=patient_id,
        resource_type=ResourceType.PATIENT,
        changes={"thread_count": len(threads)},
    )
    return PatientMessageThreadListResponse(
        data=[PatientMessageThreadResponse.from_thread(t) for t in threads],
        total=len(threads),
    )


@message_threads_router.get("/{thread_id}", response_model=PatientMessageThreadDetailResponse)
def get_thread(
    thread_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageThreadDetailResponse:
    """One thread with its messages — a PHI disclosure, recorded as one."""
    thread = _accessible_thread(thread_id, user, repo)
    messages = repo.list_messages(thread.id, user.id)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_VIEWED,
        user=user,
        request=request,
        resource_id=thread.id,
        patient_id=thread.patient_id,
        changes={"message_count": len(messages)},
    )
    return PatientMessageThreadDetailResponse.from_thread_with_messages(thread, messages)


@message_threads_router.post(
    "/{thread_id}/replies",
    status_code=status.HTTP_201_CREATED,
    response_model=PatientMessageResponse,
)
def reply_to_thread(
    thread_id: str,
    request_body: SendMessageRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_baa_acceptance),
    ctx: TenantContext = Depends(get_tenant_context),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageResponse:
    """Write back to the patient. This is the other half of the loop.

    Without it the store is somewhere a patient can write and nobody can
    answer, which is worse than not having it.
    """
    thread = _accessible_thread(thread_id, user, repo)
    message = repo.add_reply(
        PatientMessage(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            patient_id=thread.patient_id,
            sender=SENDER_CLINICIAN,
            body=request_body.body,
            created_at=utc_now(),
        ),
        user.id,
    )
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_SENT,
        user=user,
        request=request,
        resource_id=thread.id,
        patient_id=thread.patient_id,
        changes={"message_id": message.id, "sender": SENDER_CLINICIAN},
    )
    # The reply is dispatched too. A deployment mirroring this conversation
    # somewhere needs both halves of it, not the patient's half.
    _schedule_hooks(
        background_tasks,
        practice_schema=ctx.practice_schema,
        thread=thread,
        message=message,
    )
    return PatientMessageResponse.from_message(message)
