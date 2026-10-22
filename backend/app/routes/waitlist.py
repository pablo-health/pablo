# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician's waitlist: clients waiting for a better or more regular time.

  The list — ``/api/waitlist``

    GET    ""              -> the caller's open entries, in their order
    PUT    /order          -> put the named entries in this order
    PATCH  /{entry_id}     -> change days, times, modality, session type, pause, auto-offer
    DELETE /{entry_id}     -> take one off the list (returns it as it was)

  One client — ``/api/patients/{patient_id}/waitlist``

    GET    ""              -> their open entry, if any, and what to suggest when adding
    POST   ""              -> put them on the list

**Each clinician's list is their own.** Every read and write names the caller,
and the table's row policy tests the same thing underneath. Adding a client
goes through the chart's own patient lookup first, so a client this
clinician holds no grant on is "not found", never "forbidden".

**Only an active client waits.** A client on hold or no longer seen is
refused with 409 and nothing is written.

**Audit**, ids only: a read of the list records one row per client on it; a
change records the entry, the operation and the names of what changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import BadRequestError, ConflictError, NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..db import get_db_session
from ..models import AuditAction, User
from ..services import AuditService, get_audit_service
from ..waitlist.repository import WaitlistRepository
from ..waitlist.schemas import (
    AddWaitlistEntryRequest,
    PatientWaitlistResponse,
    ReorderWaitlistRequest,
    UpdateWaitlistEntryRequest,
    WaitlistEntryResponse,
    WaitlistResponse,
)
from ..waitlist.service import (
    AlreadyWaitingError,
    ClientNotEligibleError,
    OrderMismatchError,
    WaitlistNotFoundError,
    WaitlistService,
)
from ._deps import patient_not_found, visible_patient
from .patients import get_patient_repository

if TYPE_CHECKING:
    from ..repositories import PatientRepository

router = APIRouter(prefix="/api/waitlist", tags=["waitlist"])
patient_waitlist_router = APIRouter(prefix="/api/patients", tags=["waitlist"])

NOT_FOUND_MESSAGE = "This client is not on your waitlist."
NOT_ACTIVE_MESSAGE = "Only active clients can be added to the waitlist."
ALREADY_WAITING_MESSAGE = "This client is already on your waitlist."
ORDER_MISMATCH_MESSAGE = "The waitlist changed. Reload it and try again."


def get_waitlist_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> WaitlistService:
    """The service on a tenant-scoped clinician session."""
    session = get_db_session()
    return WaitlistService(WaitlistRepository(session), session)


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


@router.get("", response_model=WaitlistResponse)
def list_waitlist(
    request: Request,
    user: User = Depends(require_baa_acceptance),
    service: WaitlistService = Depends(get_waitlist_service),
    audit: AuditService = Depends(get_audit_service),
) -> WaitlistResponse:
    """The caller's open entries, top first. Audits one view per client on it."""
    entries = service.list_open(user.id)
    for e in entries:
        audit.log_waitlist_action(
            AuditAction.WAITLIST_VIEWED, user, request, e.id, e.patient_id, {"view": "list"}
        )
    return WaitlistResponse(entries=[WaitlistEntryResponse.from_entry(e) for e in entries])


@router.put("/order", response_model=WaitlistResponse)
def reorder_waitlist(
    body: ReorderWaitlistRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    service: WaitlistService = Depends(get_waitlist_service),
    audit: AuditService = Depends(get_audit_service),
) -> WaitlistResponse:
    """Put the named entries in this order, in the places they already hold."""
    try:
        entries = service.reorder(user.id, body.entry_ids)
    except OrderMismatchError as exc:
        raise ConflictError(ORDER_MISMATCH_MESSAGE) from exc
    named = set(body.entry_ids)
    for e in entries:
        if e.id in named:
            audit.log_waitlist_action(
                AuditAction.WAITLIST_REORDERED,
                user,
                request,
                e.id,
                e.patient_id,
                {"position": e.position},
            )
    return WaitlistResponse(entries=[WaitlistEntryResponse.from_entry(e) for e in entries])


@router.patch("/{entry_id}", response_model=WaitlistEntryResponse)
def update_waitlist_entry(
    entry_id: str,
    body: UpdateWaitlistEntryRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    service: WaitlistService = Depends(get_waitlist_service),
    audit: AuditService = Depends(get_audit_service),
) -> WaitlistEntryResponse:
    """Change what an entry wants, or pause it, or switch automatic offers. Its place stays."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise BadRequestError("Nothing to change.")
    if any(fields[k] is None for k in fields if k != "session_type"):
        raise BadRequestError("Only the session type can be cleared.")
    try:
        changed = service.change(user.id, entry_id, fields)
    except WaitlistNotFoundError as exc:
        raise NotFoundError(NOT_FOUND_MESSAGE, {"entry_id": entry_id}) from exc
    audit.log_waitlist_action(
        AuditAction.WAITLIST_ENTRY_UPDATED,
        user,
        request,
        changed.id,
        changed.patient_id,
        {"fields": sorted(fields)},
    )
    return WaitlistEntryResponse.from_entry(changed)


@router.delete("/{entry_id}", response_model=WaitlistEntryResponse)
def remove_waitlist_entry(
    entry_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    service: WaitlistService = Depends(get_waitlist_service),
    audit: AuditService = Depends(get_audit_service),
) -> WaitlistEntryResponse:
    """Take an entry off the list and close the gap. The entry comes back as it was."""
    try:
        removed = service.remove(user.id, entry_id)
    except WaitlistNotFoundError as exc:
        raise NotFoundError(NOT_FOUND_MESSAGE, {"entry_id": entry_id}) from exc
    audit.log_waitlist_action(
        AuditAction.WAITLIST_ENTRY_REMOVED,
        user,
        request,
        removed.id,
        removed.patient_id,
        {"position": removed.position},
    )
    return WaitlistEntryResponse.from_entry(removed)


# ---------------------------------------------------------------------------
# One client
# ---------------------------------------------------------------------------


@patient_waitlist_router.get("/{patient_id}/waitlist", response_model=PatientWaitlistResponse)
def get_patient_waitlist(
    patient_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: WaitlistService = Depends(get_waitlist_service),
    audit: AuditService = Depends(get_audit_service),
) -> PatientWaitlistResponse:
    """The client's place on the caller's list, and the defaults for adding them."""
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    entry = service.open_entry_for(user.id, patient.id)
    audit.log_patient_action(
        AuditAction.WAITLIST_VIEWED, user, request, patient, {"view": "client"}
    )
    return PatientWaitlistResponse(
        entry=None if entry is None else WaitlistEntryResponse.from_entry(entry),
        usual_session_type=service.usual_session_type(patient.id),
        past_modalities=sorted(service.past_modalities(patient.id)),  # type: ignore[arg-type]
    )


@patient_waitlist_router.post(
    "/{patient_id}/waitlist",
    status_code=status.HTTP_201_CREATED,
    response_model=WaitlistEntryResponse,
)
def add_to_waitlist(
    patient_id: str,
    body: AddWaitlistEntryRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: WaitlistService = Depends(get_waitlist_service),
    audit: AuditService = Depends(get_audit_service),
) -> WaitlistEntryResponse:
    """Put an active client on the caller's list: at the end, or at the place named."""
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    try:
        added = service.add(
            user.id,
            patient.id,
            patient.status,
            days=body.days,
            times=body.times,
            modality=body.modality,
            session_type=body.session_type,
            position=body.position,
            paused=body.paused,
            no_auto_offer=body.no_auto_offer,
        )
    except ClientNotEligibleError as exc:
        raise ConflictError(NOT_ACTIVE_MESSAGE, {"patient_id": patient_id}) from exc
    except AlreadyWaitingError as exc:
        raise ConflictError(ALREADY_WAITING_MESSAGE, {"patient_id": patient_id}) from exc
    audit.log_waitlist_action(
        AuditAction.WAITLIST_ENTRY_ADDED,
        user,
        request,
        added.id,
        added.patient_id,
        {"position": added.position},
    )
    return WaitlistEntryResponse.from_entry(added)
