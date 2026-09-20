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
    POST   /api/message-threads/{thread_id}/close      -> the practice is done here
    POST   /api/message-threads/{thread_id}/reopen     -> it is not done after all
    POST   /api/message-threads/{thread_id}/assign     -> who should answer
    POST   /api/message-threads/{thread_id}/read       -> the practice has looked
    GET    /api/message-threads/{thread_id}/export     -> the whole transcript

**Closing belongs to the practice.** A patient has no close route: they
cannot end a conversation the practice may still need to answer, and the
thing they actually want — "I'm done with this one, here is a new
question" — is starting a new thread, which they can already do. Writing
into a closed thread is a 409 that says so. A clinician replying into a
closed thread reopens it, because answering somebody is reopening the
conversation and making that two requests leaves a window where the
patient has an answer they cannot respond to.

**Assignment routes; it never gates.** ``assigned_user_id`` is how a group
practice divides the work, and the ``?assigned=`` filter narrows a list.
Neither narrows access: every clinician with a grant on the patient reads
and answers every one of their threads, and the row policies do not
mention the column.

**Nothing here is ever deleted, and withdrawing a patient's portal access
deletes nothing.** Their threads and messages stay exactly where they are,
with the same retention as the rest of the chart — the correspondence is
part of the record, and a patient who is invited back finds it waiting.
The patient routes simply stop resolving a principal, so they fail on
their own without a state to check. There is deliberately no soft-delete
column and no delete route on either table; the only thing that removes a
thread is deleting the patient, which cascades.

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
never a subject, never a body, never a filename.

**A message may carry files, and they are chart documents.** Every send
route takes an optional ``attachment_ids``; each id names a document the
sender already uploaded and finalized through their own document routes
with category ``message``. Nothing is uploaded here and no bytes pass
through these handlers — the link is recorded, and the file is downloaded
afterwards through whichever document route belongs to the principal
asking. What makes an id usable is checked against the caller's OWN
surface: a patient may send a file they uploaded themselves, a clinician
one they uploaded themselves onto that thread's chart, and neither may
send a file that is already on another message. A refusal is a 422 that
names the rule and says nothing about whose document it was, so the field
is not a way to ask which ids exist.

**After a message is stored, registered post-message callbacks run.** A
deployment may configure them; the default is none. See
:mod:`app.services.patient_message_hooks` for what a callback may assume.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.patient_context import PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import (
    AssignThreadRequest,
    AuditAction,
    DocumentCategory,
    MarkThreadReadResponse,
    MessageAttachment,
    PatientDocument,
    PatientMessage,
    PatientMessageResponse,
    PatientMessageThread,
    PatientMessageThreadDetailResponse,
    PatientMessageThreadListResponse,
    PatientMessageThreadResponse,
    SendMessageRequest,
    StartThreadRequest,
    ThreadExportResponse,
    User,
)
from ..models.audit import ResourceType
from ..models.patient_message import (
    SENDER_CLINICIAN,
    SENDER_PATIENT,
    THREAD_STATUS_CLOSED,
    THREAD_STATUS_OPEN,
    ThreadAssignmentFilter,
)
from ..rate_limit import get_patient_message_send_limiter
from ..repositories import PatientDocumentRepository, PatientMessageRepository
from ..repositories import get_patient_document_repository as _document_repo_factory
from ..repositories import get_patient_message_repository as _repo_factory
from ..services import AuditService, get_audit_service
from ..services.patient_message_hooks import PatientMessageEvent, dispatch_patient_message
from ..utcnow import utc_now

logger = logging.getLogger(__name__)

patient_messages_router = APIRouter(prefix="/api/patient/messages", tags=["patient-messages"])
patient_threads_router = APIRouter(prefix="/api/patients", tags=["patient-messages"])
message_threads_router = APIRouter(prefix="/api/message-threads", tags=["patient-messages"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

# What a patient is told when they write into a thread the practice closed.
# It names the one thing they can do about it and stops there.
CLOSED_THREAD_MESSAGE = "This conversation is closed. Start a new one."


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


def get_patient_document_repository() -> PatientDocumentRepository:
    """The documents repository, for checking what may be attached.

    Shared by both surfaces for the same reason the one above is, and
    unarmed for the same reason: the principal each route depends on has
    already set the ``search_path`` by the time this runs. It is the
    documents repository rather than the documents service because nothing
    here touches storage — an attachment is a row that already exists, and
    the only question is whether this caller may send it.
    """
    return _document_repo_factory()


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


def _attachments_of(
    messages: list[PatientMessage], patient_id: str, repo: PatientMessageRepository
) -> list[PatientMessage]:
    """Fill in what each message carried, in one read for the whole thread."""
    by_message = repo.list_attachments([m.id for m in messages], patient_id)
    for message in messages:
        message.attachments = by_message.get(message.id, [])
    return messages


def _as_attachments(message_id: str, documents: list[PatientDocument]) -> list[MessageAttachment]:
    """The reply payload's attachments, off the documents just validated.

    Built from what the send already read rather than read back, so the
    response cannot describe a link the write did not make.
    """
    return [
        MessageAttachment(
            message_id=message_id,
            document_id=document.id,
            filename=document.filename,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
        )
        for document in documents
    ]


def _refuse_attachment() -> NoReturn:
    """One answer for every way an id fails to be attachable.

    Unknown, unfinalized, the wrong category, somebody else's chart, not
    the caller's own upload — all of it lands here, because telling them
    apart would make this field a way to ask which document ids exist and
    who they belong to. The rule the caller can act on is in the code.
    """
    raise UnprocessableEntityError(
        "That file can't be attached.",
        code="ATTACHMENT_NOT_AVAILABLE",
    )


def _refuse_reused_attachment() -> NoReturn:
    raise UnprocessableEntityError(
        "That file has already been sent.",
        code="ATTACHMENT_ALREADY_SENT",
    )


def _usable_or_refuse(document: PatientDocument | None) -> PatientDocument:
    """What every attachment must be, whoever is sending it.

    On the chart, finished uploading, and filed as correspondence rather
    than as something else the sender happens to be able to read.
    """
    # The scan gate joins this test when the scanner seam lands: an
    # attachment will also have to be a document the scanner cleared.
    if (
        document is None
        or document.finalized_at is None
        or document.category is not DocumentCategory.MESSAGE
    ):
        _refuse_attachment()
    return document


def _not_already_sent(
    documents: list[PatientDocument], patient_id: str, repo: PatientMessageRepository
) -> None:
    """A file rides on one message or none.

    Asked only of documents already shown to be the caller's own, so the
    answer discloses nothing about anybody else's chart. The database says
    the same thing with a unique constraint; this is what turns it into a
    422 the sender can read instead of a failed write.
    """
    if repo.already_attached([d.id for d in documents], patient_id):
        _refuse_reused_attachment()


def _no_duplicates(attachment_ids: list[str]) -> None:
    """The same id twice in one send is the same rule, caught a step early."""
    if len(set(attachment_ids)) != len(attachment_ids):
        _refuse_reused_attachment()


def _patient_attachments(
    attachment_ids: list[str],
    patient: PatientContext,
    documents: PatientDocumentRepository,
    repo: PatientMessageRepository,
) -> list[PatientDocument]:
    """The documents a patient may send: their own uploads, on their own chart.

    ``get_for_patient_principal`` already narrows to this patient's chart
    and to the categories their own surface carries; the uploader test on
    top is what keeps a patient from forwarding something a clinician filed
    for them under the same category.
    """
    if not attachment_ids:
        return []
    _no_duplicates(attachment_ids)
    resolved = [
        _usable_or_refuse(documents.get_for_patient_principal(document_id, patient.patient_id))
        for document_id in attachment_ids
    ]
    for document in resolved:
        if document.uploaded_by_patient_id != patient.patient_id:
            _refuse_attachment()
    _not_already_sent(resolved, patient.patient_id, repo)
    return resolved


def _clinician_attachments(
    attachment_ids: list[str],
    user: User,
    thread: PatientMessageThread,
    documents: PatientDocumentRepository,
    repo: PatientMessageRepository,
) -> list[PatientDocument]:
    """The documents a clinician may send: their own uploads, on this chart.

    Their own rather than any they can read, which is the same rule the
    patient side keeps and for the same reason — a reply should send what
    the replier meant to send, not whatever happens to be reachable on the
    chart. Attaching a colleague's upload, or the patient's own, is a
    different act and would want a different route.
    """
    if not attachment_ids:
        return []
    _no_duplicates(attachment_ids)
    resolved = [
        _usable_or_refuse(documents.get(document_id, user.id)) for document_id in attachment_ids
    ]
    for document in resolved:
        if document.user_id != user.id or document.patient_id != thread.patient_id:
            _refuse_attachment()
    _not_already_sent(resolved, thread.patient_id, repo)
    return resolved


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
    documents: PatientDocumentRepository = Depends(get_patient_document_repository),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientMessageThreadDetailResponse:
    """Start a thread and send its first message.

    Exempt from the subscription gate like every patient route: a patient
    does not hold the practice's subscription, and a practice whose billing
    lapsed should not silently stop accepting its patients' messages.
    """
    get_patient_message_send_limiter().check(patient.patient_id)
    attached = _patient_attachments(request_body.attachment_ids, patient, documents, repo)

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
    repo.link_attachments(
        message_id=message.id,
        patient_id=patient.patient_id,
        document_ids=[d.id for d in attached],
        created_at=now,
    )
    message.attachments = _as_attachments(message.id, attached)

    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_CREATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_MESSAGE_THREAD,
        resource_id=thread.id,
        changes={"has_subject": thread.subject is not None},
    )
    # How many files went with it, never which. The document ids are on
    # their own upload records; a filename is not on any audit row.
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_MESSAGE_SENT,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_MESSAGE_THREAD,
        resource_id=thread.id,
        changes={
            "message_id": message.id,
            "sender": SENDER_PATIENT,
            "attachment_count": len(attached),
        },
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
    documents: PatientDocumentRepository = Depends(get_patient_document_repository),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientMessageResponse:
    """Append a message to one of the patient's own threads.

    A closed thread is refused rather than silently reopened: the practice
    ended this conversation, and the patient's next question deserves its
    own thread instead of arriving under a subject somebody already
    answered. That check comes before the attachments are looked at, so a
    patient whose thread was closed is told that and not told their file is
    unusable — the first answer is the one they can act on.
    """
    get_patient_message_send_limiter().check(patient.patient_id)

    thread = _own_thread(thread_id, patient, repo)
    if thread.status == THREAD_STATUS_CLOSED:
        raise ConflictError(CLOSED_THREAD_MESSAGE, {"thread_id": thread.id})

    attached = _patient_attachments(request_body.attachment_ids, patient, documents, repo)
    now = utc_now()
    message = repo.add_patient_message(
        PatientMessage(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            patient_id=patient.patient_id,
            sender=SENDER_PATIENT,
            body=request_body.body,
            created_at=now,
        )
    )
    repo.link_attachments(
        message_id=message.id,
        patient_id=patient.patient_id,
        document_ids=[d.id for d in attached],
        created_at=now,
    )
    message.attachments = _as_attachments(message.id, attached)
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_MESSAGE_SENT,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_MESSAGE_THREAD,
        resource_id=thread.id,
        changes={
            "message_id": message.id,
            "sender": SENDER_PATIENT,
            "attachment_count": len(attached),
        },
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
    return PatientMessageThreadDetailResponse.from_thread_with_messages(
        thread, _attachments_of(messages, patient.patient_id, repo)
    )


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
    assigned: ThreadAssignmentFilter = "all",
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageThreadListResponse:
    """The threads for one patient. Empty when the caller has no grant.

    ``assigned`` narrows a group practice's view to its own queue or to what
    nobody has picked up. It is a filter on the list and nothing more — a
    thread left out of it is still readable by id, because who may read a
    thread is the grant on the patient.

    Each row carries the count of patient messages that arrived after the
    practice last marked the thread read.

    Audited patient-scoped rather than per thread: the list discloses that
    this patient has correspondence and when it last moved, not what any of
    it says. Opening a thread is the content read, and it has its own row.
    """
    rows = repo.list_threads_for_patient(patient_id, user.id, assigned)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_VIEWED,
        user=user,
        request=request,
        resource_id=patient_id,
        patient_id=patient_id,
        resource_type=ResourceType.PATIENT,
        changes={"thread_count": len(rows), "assigned": assigned},
    )
    return PatientMessageThreadListResponse(
        data=[PatientMessageThreadResponse.from_thread(t, unread) for t, unread in rows],
        total=len(rows),
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
    messages = _attachments_of(repo.list_messages(thread.id, user.id), thread.patient_id, repo)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_VIEWED,
        user=user,
        request=request,
        resource_id=thread.id,
        patient_id=thread.patient_id,
        changes={
            "message_count": len(messages),
            "attachment_count": sum(len(m.attachments) for m in messages),
        },
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
    documents: PatientDocumentRepository = Depends(get_patient_document_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageResponse:
    """Write back to the patient. This is the other half of the loop.

    Without it the store is somewhere a patient can write and nobody can
    answer, which is worse than not having it. A reply may carry files on
    the same terms the patient's own send does — see the module docstring.

    Replying into a closed thread reopens it, in the repository's own
    transaction, and the reopening is recorded alongside the send — two
    facts about one request rather than two requests. A closed thread is
    therefore no obstacle to attaching: the reply is what reopens it, so
    there is nothing here for the attachment rules to refuse.
    """
    thread = _accessible_thread(thread_id, user, repo)
    was_closed = thread.status == THREAD_STATUS_CLOSED
    attached = _clinician_attachments(request_body.attachment_ids, user, thread, documents, repo)
    now = utc_now()
    message = repo.add_reply(
        PatientMessage(
            id=str(uuid.uuid4()),
            thread_id=thread.id,
            patient_id=thread.patient_id,
            sender=SENDER_CLINICIAN,
            body=request_body.body,
            created_at=now,
        ),
        user.id,
    )
    repo.link_attachments(
        message_id=message.id,
        patient_id=thread.patient_id,
        document_ids=[d.id for d in attached],
        created_at=now,
    )
    message.attachments = _as_attachments(message.id, attached)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_SENT,
        user=user,
        request=request,
        resource_id=thread.id,
        patient_id=thread.patient_id,
        changes={
            "message_id": message.id,
            "sender": SENDER_CLINICIAN,
            "attachment_count": len(attached),
        },
    )
    if was_closed:
        audit.log_patient_message_action(
            action=AuditAction.PATIENT_MESSAGE_THREAD_REOPENED,
            user=user,
            request=request,
            resource_id=thread.id,
            patient_id=thread.patient_id,
            changes={"by": "reply", "message_id": message.id},
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


@message_threads_router.post("/{thread_id}/close", response_model=PatientMessageThreadResponse)
def close_thread(
    thread_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageThreadResponse:
    """Say the practice is finished with this conversation.

    Closing an already-closed thread is a no-op that keeps the original
    ``closed_by`` and ``closed_at``: the fact worth having later is who
    ended the conversation, not who pressed the button most recently.
    """
    thread = _accessible_thread(thread_id, user, repo)
    closed = repo.close_thread(thread.id, user.id, utc_now())
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_CLOSED,
        user=user,
        request=request,
        resource_id=closed.id,
        patient_id=closed.patient_id,
        changes={"closed_at": closed.closed_at.isoformat() if closed.closed_at else None},
    )
    return PatientMessageThreadResponse.from_thread(closed)


@message_threads_router.post("/{thread_id}/reopen", response_model=PatientMessageThreadResponse)
def reopen_thread(
    thread_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageThreadResponse:
    """Put a closed thread back into circulation without writing into it."""
    thread = _accessible_thread(thread_id, user, repo)
    reopened = repo.reopen_thread(thread.id, user.id)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_REOPENED,
        user=user,
        request=request,
        resource_id=reopened.id,
        patient_id=reopened.patient_id,
        changes={"by": "request"},
    )
    return PatientMessageThreadResponse.from_thread(reopened)


@message_threads_router.post("/{thread_id}/assign", response_model=PatientMessageThreadResponse)
def assign_thread(
    thread_id: str,
    request_body: AssignThreadRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageThreadResponse:
    """Point the thread at a clinician, or at nobody with ``{"user_id": null}``.

    Who is assigned changes which queue the thread shows up in and nothing
    else. It grants the assignee no access they did not already have, and it
    takes none away from anybody else.
    """
    thread = _accessible_thread(thread_id, user, repo)
    assigned = repo.assign_thread(thread.id, user.id, request_body.user_id)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_ASSIGNED,
        user=user,
        request=request,
        resource_id=assigned.id,
        patient_id=assigned.patient_id,
        changes={"assigned_user_id": assigned.assigned_user_id},
    )
    return PatientMessageThreadResponse.from_thread(assigned)


@message_threads_router.post("/{thread_id}/read", response_model=PatientMessageThreadResponse)
def mark_thread_read_by_clinician(
    thread_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> PatientMessageThreadResponse:
    """Mark the thread as seen by the practice, which zeroes its unread count.

    Audited under the same action as the patient's own mark-read, separated
    by ``actor_type`` the way sends already are: "this was seen, and when"
    is the fact, and who saw it is a different column.
    """
    thread = _accessible_thread(thread_id, user, repo)
    marked = repo.mark_thread_read_by_clinician(thread.id, user.id, utc_now())
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_READ,
        user=user,
        request=request,
        resource_id=marked.id,
        patient_id=marked.patient_id,
        changes={
            "clinician_last_read_at": marked.clinician_last_read_at.isoformat()
            if marked.clinician_last_read_at
            else None
        },
    )
    return PatientMessageThreadResponse.from_thread(marked)


@message_threads_router.get("/{thread_id}/export", response_model=ThreadExportResponse)
def export_thread(
    thread_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: PatientMessageRepository = Depends(get_patient_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ThreadExportResponse:
    """The whole conversation as a transcript, for filing or handing on.

    Machine-readable JSON, not a rendered document: whatever assembles a
    chart export decides how this should look on a page. The disclosure is
    the same one ``GET /{thread_id}`` makes, so it is recorded the same way
    under its own action.
    """
    thread = _accessible_thread(thread_id, user, repo)
    messages = repo.list_messages(thread.id, user.id)
    audit.log_patient_message_action(
        action=AuditAction.PATIENT_MESSAGE_THREAD_EXPORTED,
        user=user,
        request=request,
        resource_id=thread.id,
        patient_id=thread.patient_id,
        changes={"message_count": len(messages)},
    )
    return ThreadExportResponse(
        thread=PatientMessageThreadResponse.from_thread(thread),
        messages=[PatientMessageResponse.from_message(m) for m in messages],
    )
