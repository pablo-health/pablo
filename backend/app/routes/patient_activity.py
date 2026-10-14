# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Record activity: who acted on a client's record, and when.

* ``GET /api/patients/{patient_id}/activity`` — newest first, paged back
  with ``cursor``.

For the practice owner only. The practice is responsible for reviewing
activity on its clients' records (§ 164.308(a)(1)(ii)(D), § 164.312(b)),
and the owner is the one account the audit table's owner-read policy lets
see rows written by someone else — another clinician, the client through
the portal, a background job. Anyone else gets 403 rather than a trail
silently reduced to their own rows.

Actions only, never content: no ``changes``, ``ip_address`` or
``user_agent`` leaves this route. Reading it is itself an access, so it
writes a ``patient_activity_viewed`` row of its own.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from ..api_errors import BadRequestError, ForbiddenError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..db import get_db_session
from ..db.platform_models import PracticeRow
from ..db.practice_owner import is_recorded_owner
from ..models import AuditAction, User
from ..models.audit import (
    ACTOR_TYPE_ANONYMOUS,
    ACTOR_TYPE_CLINICIAN,
    ACTOR_TYPE_PATIENT,
    ACTOR_TYPE_PLATFORM_STAFF,
    ACTOR_TYPE_SYSTEM,
    AuditCursor,
    AuditLogEntry,
)
from ..repositories import (
    PatientRepository,
    UserRepository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from .patients import get_patient_repository

router = APIRouter(prefix="/api/patients", tags=["patients"])

ActorKind = Literal["clinician", "staff", "patient", "system", "anonymous"]

_ACTOR_KINDS: dict[str, ActorKind] = {
    ACTOR_TYPE_CLINICIAN: "clinician",
    ACTOR_TYPE_PLATFORM_STAFF: "staff",
    ACTOR_TYPE_PATIENT: "patient",
    ACTOR_TYPE_SYSTEM: "system",
    ACTOR_TYPE_ANONYMOUS: "anonymous",
}

#: Actor kinds whose ``user_id`` names a person who acted, and so has a name.
#: For ``system`` and ``anonymous`` rows it names the scope, not the actor.
_NAMED_ACTORS = frozenset({ACTOR_TYPE_CLINICIAN, ACTOR_TYPE_PLATFORM_STAFF})

RECORD_ACTIVITY_MAX_LIMIT = 200


class RecordActivityItem(BaseModel):
    id: str
    timestamp: str
    actor_type: ActorKind
    #: The clinician's or staff member's name; the component for a job.
    #: Null for the client, whose name the chart already shows.
    actor_name: str | None = None
    #: The row is the reader's own.
    is_you: bool
    action: str
    resource_type: str


class RecordActivityResponse(BaseModel):
    data: list[RecordActivityItem]
    limit: int
    # Present when the page filled to ``limit``: older rows may sit behind it.
    next_cursor: str | None = None


def _practice(practice_id: str | None) -> PracticeRow | None:
    return get_db_session().get(PracticeRow, practice_id) if practice_id else None


def _require_recorded_owner(ctx: TenantContext, user: User) -> None:
    if not is_recorded_owner(_practice(ctx.practice_id), user.id):
        raise ForbiddenError(
            "Only the practice owner can see a record's activity",
            code="NOT_PRACTICE_OWNER",
        )


def _actor_names(entries: list[AuditLogEntry], users: UserRepository) -> dict[str, str]:
    ids = {e.user_id for e in entries if e.actor_type in _NAMED_ACTORS}
    found = {user_id: users.get(user_id) for user_id in ids}
    return {user_id: account.name for user_id, account in found.items() if account}


def _item(entry: AuditLogEntry, names: dict[str, str], reader_id: str) -> RecordActivityItem:
    if entry.actor_type in _NAMED_ACTORS:
        name = names.get(entry.user_id)
    elif entry.actor_type == ACTOR_TYPE_SYSTEM:
        name = entry.actor_component
    else:
        name = None
    return RecordActivityItem(
        id=entry.id,
        timestamp=entry.timestamp,
        actor_type=_ACTOR_KINDS.get(entry.actor_type, "system"),
        actor_name=name,
        is_you=entry.actor_type == ACTOR_TYPE_CLINICIAN and entry.user_id == reader_id,
        action=entry.action,
        resource_type=entry.resource_type,
    )


@router.get("/{patient_id}/activity", response_model=RecordActivityResponse)
def get_record_activity(
    patient_id: str,
    request: Request,
    cursor: str | None = Query(
        None, description="Page further back: the `next_cursor` of the previous page."
    ),
    limit: int = Query(50, ge=1, le=RECORD_ACTIVITY_MAX_LIMIT),
    user: User = Depends(require_baa_acceptance),
    ctx: TenantContext = Depends(get_tenant_context),
    patients: PatientRepository = Depends(get_patient_repository),
    users: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> RecordActivityResponse:
    """Who acted on this client's record, newest first.

    The owner check comes before the client lookup, so a non-owner learns
    nothing about which clients exist. An owner without access to the
    client gets the same 404 as any other chart route.
    """
    _require_recorded_owner(ctx, user)

    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    before = None
    if cursor is not None:
        try:
            before = AuditCursor.decode(cursor)
        except ValueError as exc:
            raise BadRequestError("Invalid cursor.") from exc

    entries = audit.list_for_patient(patient_id=patient.id, limit=limit, before=before)
    names = _actor_names(entries, users)
    # Written after the read, so it lands newer than this page and never
    # appears in a page reached through ``cursor``.
    audit.log_patient_action(
        AuditAction.PATIENT_ACTIVITY_VIEWED,
        user,
        request,
        patient,
        changes={"returned_count": len(entries)},
    )
    next_cursor = AuditCursor.from_entry(entries[-1]).encode() if len(entries) == limit else None
    return RecordActivityResponse(
        data=[_item(e, names, user.id) for e in entries],
        limit=limit,
        next_cursor=next_cursor,
    )
