# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Refill requests: a patient asks, a prescriber answers.

Two surfaces, two routers:

  Patient portal — ``/api/patient/refills`` (mounted when ``refills`` is in
  ``PORTAL_MODULES``)

    GET    /medications    -> the patient's active medications, to pick from
    GET    ""              -> the patient's own requests, newest first
    POST   ""              -> ask for a refill

  Clinician — ``/api/refill-requests`` (always mounted)

    GET    ?view=pending|recent   -> the practice's queue, or what was just answered
    GET    /{request_id}          -> one request
    POST   /{request_id}/decision -> answer it

**A request is not a message.** It has exactly one answer, the answer is a
decision, and a prescriber records it here rather than replying. Nothing in
this module writes to the secure-messaging tables.

**The patient id comes from the principal**, never from the request, and a
patient never sets a status: every request starts as ``requested``, and the
decision route is the only thing that moves it.

**A decision is made once.** Answering a request that has already been
answered — including by a colleague a moment earlier — is a 409, and the
first answer stands.

**Nothing is sent to a pharmacy.** The engine records that the prescriber
sent the prescription, asked for a visit, or declined; it transmits nothing.
What a decision may send is a portal notice telling the patient their
request was answered: a name and a link, never the medication or the
decision (see :mod:`app.portal.notices`).

**Audit follows who is acting**, as on secure messaging: a patient reading
their own requests is not a disclosure; asking is recorded, and so is every
clinician read and decision. Payloads carry ids and the decision — never a
medication, never a note.

**After a request is stored or decided, registered callbacks run.** A
deployment may configure them; the default is none. See
:mod:`app.services.refill_request_hooks`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request, status

from ..api_errors import ConflictError, NotFoundError
from ..auth.patient_context import PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import AuditAction, User
from ..models.audit import ResourceType
from ..models.refill_request import REFILL_STATUS_REQUESTED, RefillRequest
from ..models.refill_request_api import (
    CreateRefillRequest,
    DecideRefillRequest,
    PatientMedicationOption,
    PatientMedicationOptionList,
    PatientRefillRequestList,
    PatientRefillRequestResponse,
    RefillRequestList,
    RefillRequestResponse,
)
from ..portal.factory import get_notice_delivery
from ..portal.notices import send_portal_notice
from ..rate_limit import get_refill_request_limiter
from ..repositories import RefillRequestRepository
from ..repositories import get_refill_request_repository as _repo_factory
from ..repositories.refill_request import (
    RefillQueueView,
    RefillRequestAccessDeniedError,
    RefillRequestAlreadyDecidedError,
)
from ..services import AuditService, get_audit_service
from ..services.refill_request_hooks import (
    RefillRequestEvent,
    RefillRequestEventKind,
    dispatch_refill_request_event,
)
from ..utcnow import utc_now
from .patient_intake_assignments import get_clinician_patient_repository

if TYPE_CHECKING:
    from ..portal.delivery import PortalNoticeDelivery
    from ..repositories.patient import PatientRepository

patient_refills_router = APIRouter(prefix="/api/patient/refills", tags=["refill-requests"])
refill_requests_router = APIRouter(prefix="/api/refill-requests", tags=["refill-requests"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

ALREADY_DECIDED_MESSAGE = "This request was already answered."

#: The notice a decision sends. A name and a link; see
#: :mod:`app.portal.notices` for why it can be nothing more.
DECIDED_NOTICE = "refill_request_decided"


def get_refill_request_repository() -> RefillRequestRepository:
    """The tenant-scoped repository.

    Unarmed on purpose, like the secure-messaging repository: it serves both
    surfaces, and each route below depends on exactly one principal, which
    has set the ``search_path`` by the time this runs.
    """
    return _repo_factory()


def get_clinician_refill_request_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> RefillRequestRepository:
    """The repository on a tenant-scoped clinician session."""
    return _repo_factory()


def _schedule_hooks(
    background_tasks: BackgroundTasks,
    *,
    kind: RefillRequestEventKind,
    practice_schema: str | None,
    request: RefillRequest,
) -> None:
    """Hand the stored request to whatever the deployment registered, after the response."""
    background_tasks.add_task(
        dispatch_refill_request_event,
        RefillRequestEvent(
            kind=kind,
            practice_schema=practice_schema,
            request_id=request.id,
            patient_id=request.patient_id,
            status=request.status,
            medication_text=request.medication_text,
            patient_note=request.patient_note,
            created_at=request.created_at,
            decided_at=request.decided_at,
        ),
    )


# ---------------------------------------------------------------------------
# Patient surface
# ---------------------------------------------------------------------------


@patient_refills_router.get("/medications", response_model=PatientMedicationOptionList)
def list_my_medications(
    patient: CurrentPatient,
    repo: RefillRequestRepository = Depends(get_refill_request_repository),
    _: None = Depends(subscription_exempt),
) -> PatientMedicationOptionList:
    """The patient's active medications, name and dose only. Not audited: their own record."""
    options = [
        PatientMedicationOption(id=med_id, drug_name=drug, dose=dose)
        for med_id, drug, dose in repo.list_medication_options(patient.patient_id)
    ]
    return PatientMedicationOptionList(data=options, total=len(options))


@patient_refills_router.get("", response_model=PatientRefillRequestList)
def list_my_refill_requests(
    patient: CurrentPatient,
    repo: RefillRequestRepository = Depends(get_refill_request_repository),
    _: None = Depends(subscription_exempt),
) -> PatientRefillRequestList:
    """The patient's own requests, newest first. Not audited: their own record."""
    rows = repo.list_for_patient(patient.patient_id)
    return PatientRefillRequestList(
        data=[PatientRefillRequestResponse.from_request(r) for r in rows], total=len(rows)
    )


@patient_refills_router.post(
    "", status_code=status.HTTP_201_CREATED, response_model=PatientRefillRequestResponse
)
def create_refill_request(
    body: CreateRefillRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    patient: CurrentPatient,
    repo: RefillRequestRepository = Depends(get_refill_request_repository),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientRefillRequestResponse:
    """Ask for a refill of one medication.

    A ``medication_id`` must be one of the patient's own active medications;
    anything else is a 404 that says nothing about whose it was. The name is
    copied onto the request so it keeps reading correctly if the list
    changes later.
    """
    get_refill_request_limiter().check(patient.patient_id)

    if body.medication_id is not None:
        option = repo.get_medication_option(body.medication_id, patient.patient_id)
        if option is None:
            raise NotFoundError("Medication not found", {"medication_id": body.medication_id})
        medication_text = f"{option[1]} {option[2]}".strip()
    else:
        # The validator guarantees exactly one of the two is present.
        medication_text = (body.medication_text or "").strip()

    now = utc_now()
    stored = repo.add(
        RefillRequest(
            id=str(uuid.uuid4()),
            patient_id=patient.patient_id,
            medication_id=body.medication_id,
            medication_text=medication_text,
            pharmacy_text=body.pharmacy_text,
            patient_note=body.patient_note,
            status=REFILL_STATUS_REQUESTED,
            created_at=now,
            updated_at=now,
        )
    )
    audit.log_patient_principal_action(
        action=AuditAction.REFILL_REQUEST_CREATED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.REFILL_REQUEST,
        resource_id=stored.id,
        changes={
            "from_medication_list": body.medication_id is not None,
            "has_note": body.patient_note is not None,
        },
    )
    _schedule_hooks(
        background_tasks,
        kind="submitted",
        practice_schema=patient.practice_schema,
        request=stored,
    )
    return PatientRefillRequestResponse.from_request(stored)


# ---------------------------------------------------------------------------
# Clinician surface
# ---------------------------------------------------------------------------


@refill_requests_router.get("", response_model=RefillRequestList)
def list_refill_queue(
    request: Request,
    view: Literal["pending", "recent"] = "pending",
    user: User = Depends(require_baa_acceptance),
    repo: RefillRequestRepository = Depends(get_clinician_refill_request_repository),
    audit: AuditService = Depends(get_audit_service),
) -> RefillRequestList:
    """Requests on every patient the caller treats.

    ``pending`` is the queue, oldest first; ``recent`` is the last answers
    given, newest first. Audited once per patient on the list, because what
    the list discloses is per patient: that they asked, and for what.
    """
    queue_view: RefillQueueView = view
    entries = repo.list_queue(user.id, queue_view)
    per_patient: dict[str, int] = {}
    for entry in entries:
        per_patient[entry.request.patient_id] = per_patient.get(entry.request.patient_id, 0) + 1
    for patient_id, count in per_patient.items():
        audit.log_refill_request_action(
            action=AuditAction.REFILL_REQUEST_QUEUE_VIEWED,
            user=user,
            request=request,
            resource_id=patient_id,
            patient_id=patient_id,
            changes={"view": view, "request_count": count},
        )
    return RefillRequestList(
        data=[RefillRequestResponse.from_queue_entry(e) for e in entries], total=len(entries)
    )


@refill_requests_router.get("/{request_id}", response_model=RefillRequestResponse)
def get_refill_request(
    request_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    repo: RefillRequestRepository = Depends(get_clinician_refill_request_repository),
    audit: AuditService = Depends(get_audit_service),
) -> RefillRequestResponse:
    """One request. A request the caller has no grant on is a 404."""
    entry = repo.get_entry(request_id, user.id)
    if entry is None:
        raise NotFoundError("Refill request not found", {"request_id": request_id})
    audit.log_refill_request_action(
        action=AuditAction.REFILL_REQUEST_VIEWED,
        user=user,
        request=request,
        resource_id=request_id,
        patient_id=entry.request.patient_id,
    )
    return RefillRequestResponse.from_queue_entry(entry)


@refill_requests_router.post("/{request_id}/decision", response_model=RefillRequestResponse)
def decide_refill_request(
    request_id: str,
    body: DecideRefillRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(require_baa_acceptance),
    ctx: TenantContext = Depends(get_tenant_context),
    repo: RefillRequestRepository = Depends(get_clinician_refill_request_repository),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    notices: PortalNoticeDelivery = Depends(get_notice_delivery),
    audit: AuditService = Depends(get_audit_service),
) -> RefillRequestResponse:
    """Record the prescriber's answer. Once.

    On success the patient is told their request was answered, if the
    deployment has wired a channel and the chart has an address. The message
    is the notice's name and a link to the practice's portal page, where the
    status is read after sign-in. Best effort by design: the decision is
    already recorded, and a mail server being down is not a reason to lose it.
    """
    try:
        decided = repo.decide(
            request_id,
            user.id,
            status=body.status,
            prescriber_note=body.prescriber_note,
            decided_at=utc_now(),
        )
    except RefillRequestAccessDeniedError as exc:
        raise NotFoundError("Refill request not found", {"request_id": request_id}) from exc
    except RefillRequestAlreadyDecidedError as exc:
        raise ConflictError(ALREADY_DECIDED_MESSAGE, {"request_id": request_id}) from exc

    audit.log_refill_request_action(
        action=AuditAction.REFILL_REQUEST_DECIDED,
        user=user,
        request=request,
        resource_id=decided.id,
        patient_id=decided.patient_id,
        changes={"status": decided.status, "has_note": decided.prescriber_note is not None},
    )
    _schedule_hooks(
        background_tasks,
        kind="decided",
        practice_schema=ctx.practice_schema,
        request=decided,
    )
    patient = patients.get(decided.patient_id, user.id)
    send_portal_notice(
        notices,
        notice=DECIDED_NOTICE,
        to_email=patient.email if patient is not None else None,
        from_clinician_email=user.email,
    )
    return RefillRequestResponse.from_request(decided)
