# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The Inbox: everything that needs the clinician, in one list.

    GET  /api/inbox?view=open|done&kinds=...        -> the list
    GET  /api/inbox/count                           -> one number, for the badge
    POST /api/inbox/{kind}/{source_id}/dismiss      -> "I've handled this elsewhere"
    POST /api/inbox/{kind}/{source_id}/snooze       -> out of the way until a time
    POST /api/inbox/{kind}/{source_id}/restore      -> back to Open
    POST /api/inbox/portal_message/{id}/handle-earlier -> the client's earlier ones too

**The Inbox holds no copies.** Each item is read from its source's own table
every time (see :mod:`app.inbox.registry`), so acting on an item where it
lives — answering a refill, signing a note — is what takes it out. The
actions here are only the ones that belong to the Inbox itself, and each
is a row in ``inbox_item_states`` for this clinician alone.

**Audit.** The list discloses, per patient, that something of theirs is
waiting and what, so it is recorded once per patient on it. The count names
nobody and is recorded against the clinician. An action is recorded against
the item it touched, with the patient it belongs to.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from ..api_errors import NotFoundError, UnprocessableEntityError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..inbox.registry import InboxContext, InboxRegistry, get_inbox_registry
from ..inbox.replies import handle_earlier
from ..inbox.service import InboxService, UnknownInboxItemError
from ..models import AuditAction, User
from ..models.audit import ResourceType
from ..models.inbox import (
    DISPOSITION_DISMISSED,
    DISPOSITION_RESTORED,
    DISPOSITION_SNOOZED,
    KIND_PORTAL_MESSAGE,
    HandledEarlierResponse,
    InboxCountResponse,
    InboxItem,
    InboxListResponse,
    InboxView,
    SnoozeInboxItemRequest,
)
from ..repositories import (
    InboxItemStateRepository,
    PatientMessageRepository,
    get_inbox_item_state_repository,
    get_patient_message_repository,
)
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now

router = APIRouter(prefix="/api/inbox", tags=["inbox"])

#: The furthest a snooze may reach. Past that, the item is not snoozed but
#: forgotten, and dismissing says so honestly.
MAX_SNOOZE = timedelta(days=90)


def get_inbox_state_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> InboxItemStateRepository:
    """The state repository on a tenant-scoped clinician session."""
    return get_inbox_item_state_repository()


def get_inbox_message_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientMessageRepository:
    return get_patient_message_repository()


def get_inbox_service(
    registry: InboxRegistry = Depends(get_inbox_registry),
    states: InboxItemStateRepository = Depends(get_inbox_state_repository),
) -> InboxService:
    return InboxService(registry, states)


Inbox = Annotated[InboxService, Depends(get_inbox_service)]


def _context(user: User) -> InboxContext:
    return InboxContext(user_id=user.id, now=utc_now())


def _item_ref(item: InboxItem) -> str:
    return f"{item.kind}:{item.source_id}"


def _find(inbox: InboxService, ctx: InboxContext, kind: str, source_id: str) -> InboxItem:
    try:
        return inbox.find(ctx, kind, source_id)
    except UnknownInboxItemError as e:
        raise NotFoundError("Inbox item not found", {"kind": kind, "source_id": source_id}) from e


@router.get("", response_model=InboxListResponse)
def list_inbox(
    request: Request,
    inbox: Inbox,
    view: InboxView = "open",
    kinds: Annotated[list[str] | None, Query()] = None,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> InboxListResponse:
    """The clinician's items, open or done. ``kinds`` narrows to those kinds."""
    ctx = _context(user)
    items = inbox.list_items(ctx, view, set(kinds) if kinds else None)
    per_patient: dict[str, list[InboxItem]] = {}
    for item in items:
        if item.patient_id:
            per_patient.setdefault(item.patient_id, []).append(item)
    for patient_id, theirs in per_patient.items():
        audit.log_inbox_action(
            AuditAction.INBOX_VIEWED,
            user,
            request,
            resource_id=patient_id,
            patient_id=patient_id,
            resource_type=ResourceType.PATIENT,
            changes={
                "view": view,
                "item_count": len(theirs),
                "kinds": sorted({item.kind for item in theirs}),
            },
        )
    return InboxListResponse(data=items, total=len(items))


@router.get("/count", response_model=InboxCountResponse)
def count_inbox(
    request: Request,
    inbox: Inbox,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> InboxCountResponse:
    """How many items are open, for the one badge beside Inbox."""
    count = inbox.count(_context(user))
    audit.log(
        AuditAction.INBOX_COUNTED,
        user,
        request,
        resource_type=ResourceType.SELF,
        resource_id=user.id,
        changes={"open_items": count},
    )
    return InboxCountResponse(count=count)


def _record(
    inbox: InboxService,
    user: User,
    kind: str,
    source_id: str,
    disposition: str,
    snoozed_until: datetime | None = None,
) -> InboxItem:
    """Record *disposition* on an item this clinician can see, else 404."""
    ctx = _context(user)
    item = _find(inbox, ctx, kind, source_id)
    return inbox.record(ctx, item, disposition, snoozed_until=snoozed_until)


@router.post("/portal_message/{source_id}/handle-earlier", response_model=HandledEarlierResponse)
def handle_earlier_messages(
    source_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    states: InboxItemStateRepository = Depends(get_inbox_state_repository),
    messages: PatientMessageRepository = Depends(get_inbox_message_repository),
    audit: AuditService = Depends(get_audit_service),
) -> HandledEarlierResponse:
    """Mark handled what this client sent before *source_id* and is still waiting.

    The "Yes" to the question a reply asks. Declared before the generic
    item routes so ``handle-earlier`` is never read as an action name.
    """
    try:
        patient_id, handled = handle_earlier(_context(user), states, messages, source_id)
    except UnknownInboxItemError as e:
        raise NotFoundError("Message not found", {"source_id": source_id}) from e
    audit.log_inbox_action(
        AuditAction.INBOX_EARLIER_HANDLED,
        user,
        request,
        resource_id=f"{KIND_PORTAL_MESSAGE}:{source_id}",
        patient_id=patient_id,
        changes={"handled_count": len(handled)},
    )
    return HandledEarlierResponse(handled_ids=handled)


@router.post("/{kind}/{source_id}/dismiss", response_model=InboxItem)
def dismiss_item(
    kind: str,
    source_id: str,
    request: Request,
    inbox: Inbox,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> InboxItem:
    """Take the item out of Open. It stays in Done, and can be restored."""
    item = _record(inbox, user, kind, source_id, DISPOSITION_DISMISSED)
    audit.log_inbox_action(
        AuditAction.INBOX_ITEM_DISMISSED,
        user,
        request,
        resource_id=_item_ref(item),
        patient_id=item.patient_id,
    )
    return item


@router.post("/{kind}/{source_id}/snooze", response_model=InboxItem)
def snooze_item(
    kind: str,
    source_id: str,
    body: SnoozeInboxItemRequest,
    request: Request,
    inbox: Inbox,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> InboxItem:
    """Hide the item until ``until``, when it is open again on its own."""
    now = utc_now()
    if body.until <= now:
        raise UnprocessableEntityError("Choose a time in the future.", code="SNOOZE_IN_PAST")
    if body.until > now + MAX_SNOOZE:
        raise UnprocessableEntityError("Choose a time within 90 days.", code="SNOOZE_TOO_FAR")
    item = _record(inbox, user, kind, source_id, DISPOSITION_SNOOZED, body.until)
    audit.log_inbox_action(
        AuditAction.INBOX_ITEM_SNOOZED,
        user,
        request,
        resource_id=_item_ref(item),
        patient_id=item.patient_id,
        changes={"snoozed_until": body.until.isoformat()},
    )
    return item


@router.post("/{kind}/{source_id}/restore", response_model=InboxItem)
def restore_item(
    kind: str,
    source_id: str,
    request: Request,
    inbox: Inbox,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> InboxItem:
    """Put a handled, dismissed or snoozed item back in Open.

    Open again in the Inbox's eyes; if its source has since finished with it
    (the refill was answered), it stays out, because the source no longer
    lists it.
    """
    item = _record(inbox, user, kind, source_id, DISPOSITION_RESTORED)
    audit.log_inbox_action(
        AuditAction.INBOX_ITEM_RESTORED,
        user,
        request,
        resource_id=_item_ref(item),
        patient_id=item.patient_id,
    )
    return item
