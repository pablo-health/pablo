# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Homework: what a client is asked to do between visits, and whether they did it.

Two surfaces, two routers:

  Clinician — ``/api/patients/{patient_id}/homework`` (always mounted)

    GET    ""                          -> the assignments
    POST   ""                          -> add one by hand (assigned at once)
    POST   /{assignment_id}/withdraw   -> take one back off the client's list
    PATCH  /{assignment_id}            -> reword one the client already has

  Patient portal — ``/api/patient/homework`` (mounted when ``homework`` is in
  ``PORTAL_MODULES``)

    GET    ""                          -> the client's open and done assignments
    POST   /{assignment_id}/done       -> mark done, with an optional short note
    POST   /{assignment_id}/undo       -> not done after all

**Pablo suggests; the clinician assigns.** A note's homework is suggested as
chart proposals of the ``homework`` family, made in the note's chart-update
step and decided with the rest at signing (``app.routes.note_chart_proposals``);
accepting one is what creates the assignment. Nothing reaches the client
before that.

**The patient id comes from the principal** on the portal, never from the
request; on the clinician side the chart's own lookup proves the grant
before anything is read. A change that no longer applies — withdrawing what
a colleague already withdrew — is a 409, and the first answer stands.

**Audit follows who is acting**, ids and counts only: a client reading their
own homework is not a disclosure; marking it is recorded, and so is every
clinician read and change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import ConflictError, NotFoundError
from ..auth.patient_context import PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..db import get_db_session
from ..homework.repository import HomeworkRepository
from ..homework.schemas import (
    AddHomeworkRequest,
    EditHomeworkRequest,
    HomeworkAssignmentResponse,
    HomeworkOverviewResponse,
    MarkHomeworkDoneRequest,
    PatientHomeworkList,
    PatientHomeworkResponse,
)
from ..homework.service import (
    HomeworkDecisionError,
    HomeworkNotFoundError,
    HomeworkService,
    Overview,
)
from ..models import AuditAction, User
from ..models.audit import ResourceType
from ..services import AuditService, get_audit_service
from ._deps import patient_not_found, visible_patient
from .patients import get_patient_repository

if TYPE_CHECKING:
    from ..homework.repository import Assignment
    from ..repositories import PatientRepository

router = APIRouter(prefix="/api/patients", tags=["homework"])
patient_homework_router = APIRouter(prefix="/api/patient/homework", tags=["homework"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

NOT_FOUND_MESSAGE = "Homework not found"


def get_clinician_homework_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> HomeworkService:
    """The service on a tenant-scoped clinician session."""
    return HomeworkService(HomeworkRepository(get_db_session()))


def get_patient_homework_service() -> HomeworkService:
    """The service on the patient principal's session, which set the ``search_path``."""
    return HomeworkService(HomeworkRepository(get_db_session()))


def _overview(o: Overview) -> HomeworkOverviewResponse:
    return HomeworkOverviewResponse(
        assignments=[HomeworkAssignmentResponse.from_assignment(a) for a in o.assignments],
    )


# ---------------------------------------------------------------------------
# Clinician surface
# ---------------------------------------------------------------------------


@router.get("/{patient_id}/homework", response_model=HomeworkOverviewResponse)
def get_homework(
    patient_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: HomeworkService = Depends(get_clinician_homework_service),
    audit: AuditService = Depends(get_audit_service),
) -> HomeworkOverviewResponse:
    """The client's homework. Reading changes nothing; it audits one view."""
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    overview = service.overview(patient.id)
    audit.log_patient_action(AuditAction.HOMEWORK_VIEWED, user, request, patient)
    return _overview(overview)


@router.post(
    "/{patient_id}/homework",
    status_code=status.HTTP_201_CREATED,
    response_model=HomeworkAssignmentResponse,
)
def add_homework(
    patient_id: str,
    body: AddHomeworkRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: HomeworkService = Depends(get_clinician_homework_service),
    audit: AuditService = Depends(get_audit_service),
) -> HomeworkAssignmentResponse:
    """An assignment the clinician writes. Assigned at once: the clinician is the one assigning."""
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    added = service.add(patient.id, body.text, user.id)
    audit.log(
        action=AuditAction.HOMEWORK_ADDED,
        user=user,
        request=request,
        resource_type=ResourceType.HOMEWORK_ASSIGNMENT,
        resource_id=added.id,
        patient=patient,
    )
    return HomeworkAssignmentResponse.from_assignment(added)


@router.post(
    "/{patient_id}/homework/{assignment_id}/withdraw",
    response_model=HomeworkAssignmentResponse,
)
def withdraw_homework(
    patient_id: str,
    assignment_id: str,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: HomeworkService = Depends(get_clinician_homework_service),
    audit: AuditService = Depends(get_audit_service),
) -> HomeworkAssignmentResponse:
    """Take an assignment back: off the client's list, kept on the record as withdrawn."""
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    try:
        withdrawn = service.withdraw(patient.id, assignment_id, user.id)
    except HomeworkNotFoundError as exc:
        raise NotFoundError(NOT_FOUND_MESSAGE, {"assignment_id": assignment_id}) from exc
    except HomeworkDecisionError as exc:
        raise ConflictError(
            "This homework was already withdrawn.", {"assignment_id": assignment_id}
        ) from exc
    audit.log(
        action=AuditAction.HOMEWORK_WITHDRAWN,
        user=user,
        request=request,
        resource_type=ResourceType.HOMEWORK_ASSIGNMENT,
        resource_id=withdrawn.id,
        patient=patient,
    )
    return HomeworkAssignmentResponse.from_assignment(withdrawn)


@router.patch("/{patient_id}/homework/{assignment_id}", response_model=HomeworkAssignmentResponse)
def edit_homework(
    patient_id: str,
    assignment_id: str,
    body: EditHomeworkRequest,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_patient_repository),
    service: HomeworkService = Depends(get_clinician_homework_service),
    audit: AuditService = Depends(get_audit_service),
) -> HomeworkAssignmentResponse:
    """Reword an open assignment. Done or withdrawn ones keep the words they had."""
    patient = visible_patient(patients, patient_id, user.id, patient_not_found)
    try:
        edited = service.edit(patient.id, assignment_id, body.text)
    except HomeworkNotFoundError as exc:
        raise NotFoundError(NOT_FOUND_MESSAGE, {"assignment_id": assignment_id}) from exc
    except HomeworkDecisionError as exc:
        raise ConflictError(
            "Only open homework can be reworded.", {"assignment_id": assignment_id}
        ) from exc
    audit.log(
        action=AuditAction.HOMEWORK_EDITED,
        user=user,
        request=request,
        resource_type=ResourceType.HOMEWORK_ASSIGNMENT,
        resource_id=edited.id,
        patient=patient,
    )
    return HomeworkAssignmentResponse.from_assignment(edited)


# ---------------------------------------------------------------------------
# Patient surface
# ---------------------------------------------------------------------------


@patient_homework_router.get("", response_model=PatientHomeworkList)
def list_my_homework(
    patient: CurrentPatient,
    service: HomeworkService = Depends(get_patient_homework_service),
    _: None = Depends(subscription_exempt),
) -> PatientHomeworkList:
    """The client's confirmed homework, open first. Not audited: their own record."""
    rows = service.list_for_client(patient.patient_id)
    return PatientHomeworkList(
        data=[PatientHomeworkResponse.from_assignment(r) for r in rows], total=len(rows)
    )


def _marked(
    service: HomeworkService,
    patient: PatientContext,
    assignment_id: str,
    *,
    done: bool,
    note: str | None = None,
) -> Assignment:
    try:
        return service.mark(patient.patient_id, assignment_id, done=done, note=note)
    except HomeworkNotFoundError as exc:
        raise NotFoundError(NOT_FOUND_MESSAGE, {"assignment_id": assignment_id}) from exc
    except HomeworkDecisionError as exc:
        message = "This is already marked done." if done else "This is not marked done."
        raise ConflictError(message, {"assignment_id": assignment_id}) from exc


@patient_homework_router.post("/{assignment_id}/done", response_model=PatientHomeworkResponse)
def mark_homework_done(
    assignment_id: str,
    body: MarkHomeworkDoneRequest,
    request: Request,
    patient: CurrentPatient,
    service: HomeworkService = Depends(get_patient_homework_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientHomeworkResponse:
    """Mark one done, with an optional short note for the clinician."""
    marked = _marked(service, patient, assignment_id, done=True, note=body.note)
    audit.log_patient_principal_action(
        action=AuditAction.HOMEWORK_MARKED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.HOMEWORK_ASSIGNMENT,
        resource_id=marked.id,
        changes={"done": True, "has_note": body.note is not None},
    )
    return PatientHomeworkResponse.from_assignment(marked)


@patient_homework_router.post("/{assignment_id}/undo", response_model=PatientHomeworkResponse)
def mark_homework_not_done(
    assignment_id: str,
    request: Request,
    patient: CurrentPatient,
    service: HomeworkService = Depends(get_patient_homework_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> PatientHomeworkResponse:
    """Not done after all: back to open, the note cleared."""
    marked = _marked(service, patient, assignment_id, done=False)
    audit.log_patient_principal_action(
        action=AuditAction.HOMEWORK_MARKED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.HOMEWORK_ASSIGNMENT,
        resource_id=marked.id,
        changes={"done": False, "has_note": False},
    )
    return PatientHomeworkResponse.from_assignment(marked)
