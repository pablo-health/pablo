# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Asking somebody to fill a form in, and them filling it in.

The form builder decides what a practice asks. These routes are what
happens next: a clinician sends a published version to a patient, and the
patient works through it a question at a time from the portal. Two
surfaces, two routers:

  Patient portal — ``/api/patient/intake``

    GET  /assignments                              -> the forms I was asked for
    GET  /assignments/{id}                         -> one form, with what I saved
    PUT  /assignments/{id}/items/{item_id}         -> save one answer
    POST /assignments/{id}/submit                  -> hand it in, get a receipt

  Clinician — ``/api/patients``

    POST /{patient_id}/intake-assignments          -> ask for a form
    GET  /{patient_id}/intake-assignments          -> what was asked, and how far
    GET  /{patient_id}/intake-assignments/{id}     -> what they answered
    POST /{patient_id}/intake-assignments/{id}/withdraw

Five things shape all of it.

**The patient id comes from the principal, never from the request.** No
patient route takes one, so a body naming somebody else changes nothing
about which rows are read or written.

**Both patient reads and the save require step-up.** A form carries the
patient's own answers about why they came and how they have been feeling.
A link that reached the wrong inbox is one factor in a stranger's hands.

**The server decides whether a form is finished.** Every response that
mentions progress carries a ``complete`` the server computed from the rows
just now. Nothing asks the client what it thinks, which matters here more
than usual: a question somebody skipped and a question they were never
shown look the same from a browser.

**Asking twice is asking once.** Sending the same version to the same
patient again — a second click, or portal access reissued after a link
expired — returns the assignment that is already live, with a 200 rather
than a 201. There is no path that leaves somebody holding two copies of
one form.

**A status code is the whole answer.** A ``409`` means the form is no
longer this patient's to fill in, because it was handed in or withdrawn. A
``422`` on a save means the answer does not fit the question, and on a
submit it means the form is not finished and names what is outstanding.
Both messages say what to do about it in the words the patient is reading.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from ..api_errors import ConflictError, ForbiddenError, NotFoundError, UnprocessableEntityError
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..intake.answers import AnswerError
from ..intake.items import stored_config
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.patient_intake_assignment_api import (
    ClinicianIntakeAnswerResponse,
    ClinicianIntakeAssignmentDetailResponse,
    CreateAssignmentRequest,
    IntakeAssignmentDetailResponse,
    IntakeAssignmentItemResponse,
    IntakeAssignmentResponse,
    IntakeProgressResponse,
    IntakeSubmissionResponse,
    SaveAnswerRequest,
    SavedAnswerResponse,
    SubmittedMeasureResponse,
)
from ..outcome_measures.service import (  # noqa: TC001 — fastapi resolves the annotation
    OutcomeMeasureService,
)
from ..repositories import (
    get_intake_packet_repository,
    get_patient_intake_assignment_repository,
    get_patient_repository,
)
from ..services.audit_service import AuditService, get_audit_service
from ..services.patient_intake_assignment_service import (
    AssignmentClosedError,
    FrozenResponseError,
    IncompleteFormError,
    IntakeAssignmentService,
    UnpublishedVersionError,
)
from ..utcnow import utc_now
from .patient_intake import get_intake_outcome_measure_service

if TYPE_CHECKING:
    from ..intake.completion import Completion
    from ..repositories.patient import PatientRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/patient/intake", tags=["patient-intake"])

# The clinician's side, under the chart prefix every other per-patient
# surface lives at. A separate router because the two share no dependency:
# this one has a patient id in the path precisely because the caller is not
# the patient.
clinician_router = APIRouter(prefix="/api/patients", tags=["patient-intake"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]

# ``AuditService`` is spelled out in the route signatures rather than
# aliased: the route-audit guardrail matches the parameter annotation by
# name.


def get_patient_intake_assignment_service() -> IntakeAssignmentService:
    """The assignment service on whichever principal armed the session.

    No ``get_tenant_context`` dependency, unlike the clinician-only
    services elsewhere: this one is shared by both surfaces, and a patient
    request arms its schema through ``get_patient_context`` instead. Every
    route below already depends on one principal or the other, so the
    ``search_path`` is set either way by the time this runs.
    """
    return IntakeAssignmentService(
        get_patient_intake_assignment_repository(),
        get_intake_packet_repository(),
    )


def get_clinician_intake_assignment_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> IntakeAssignmentService:
    """The same service on a tenant-scoped clinician session."""
    return get_patient_intake_assignment_service()


def get_clinician_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    """The patient repository on a tenant-scoped session."""
    return get_patient_repository()


PatientAssignments = Annotated[
    IntakeAssignmentService, Depends(get_patient_intake_assignment_service)
]
ClinicianAssignments = Annotated[
    IntakeAssignmentService, Depends(get_clinician_intake_assignment_service)
]


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on every patient route here.

    Same bar as the rest of the patient intake surface, for the same
    reason: one factor reaching the wrong person should not open a chart.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


def _progress(completion: Completion) -> IntakeProgressResponse:
    return IntakeProgressResponse(complete=completion.complete, missing=completion.missing)


def _assignment_response(
    service: IntakeAssignmentService,
    assignment: dict[str, object],
    patient_id: str,
) -> IntakeAssignmentResponse:
    """One assignment with its form's name and the progress on it."""
    name, number = _version_label(service, str(assignment["version_id"]))
    return IntakeAssignmentResponse(
        id=str(assignment["id"]),
        version_id=str(assignment["version_id"]),
        packet_name=name,
        version=number,
        status=str(assignment["status"]),
        assigned_at=assignment["assigned_at"],  # type: ignore[arg-type]
        submitted_at=assignment["submitted_at"],  # type: ignore[arg-type]
        receipt_code=_optional_str(assignment.get("receipt_code")),
        progress=_progress(service.progress(assignment, patient_id)),
    )


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _version_label(service: IntakeAssignmentService, version_id: str) -> tuple[str, int]:
    """The form's name and version number, read defensively.

    A version whose template has been archived is still perfectly readable
    — archiving takes a form out of circulation without touching what was
    already sent — so nothing here treats a missing name as an error. It
    reads as an unnamed form rather than failing a list somebody needs.
    """
    version = service.version(version_id)
    if version is None:  # pragma: no cover — the assignment's foreign key holds it
        return "Intake", 1
    template = service.template(str(version["template_id"]))
    name = str(template["name"]) if template else "Intake"
    return name, int(version["version"])  # type: ignore[call-overload]


# ---------------------------------------------------------------------------
# Patient surface
# ---------------------------------------------------------------------------


@router.get("/assignments", response_model=list[IntakeAssignmentResponse])
def list_my_assignments(
    patient: CurrentPatient,
    service: PatientAssignments,
    _: None = Depends(subscription_exempt),
) -> list[IntakeAssignmentResponse]:
    """The forms this patient has been asked to fill in, newest first.

    Not audited. A patient reading their own record is not a disclosure —
    the settled principle behind the patient-principal audit model — and a
    row per portal visit would bury the disclosures that do matter.

    Exempt from the subscription gate like every patient route: a patient
    does not hold the practice's subscription, and a form they were asked
    to fill in should not fail for a billing state they cannot see.
    """
    _require_stepped_up(patient)
    return [
        _assignment_response(service, row, patient.patient_id)
        for row in service.list_for_patient(patient.patient_id)
    ]


@router.get("/assignments/{assignment_id}", response_model=IntakeAssignmentDetailResponse)
def get_my_assignment(
    assignment_id: str,
    patient: CurrentPatient,
    service: PatientAssignments,
    _: None = Depends(subscription_exempt),
) -> IntakeAssignmentDetailResponse:
    """One form, its questions in order, and whatever has been saved so far.

    Another patient's assignment id is a 404, indistinguishable from an id
    that does not exist — so the surface never confirms that somebody
    else's form is real.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)
    saved = service.answers(assignment_id, patient.patient_id)
    base = _assignment_response(service, assignment, patient.patient_id)
    return IntakeAssignmentDetailResponse(
        **base.model_dump(),
        items=[
            IntakeAssignmentItemResponse(
                id=str(row["id"]),
                key=str(row["key"]),
                position=int(row["position"]),  # type: ignore[call-overload]
                item_type=str(row["item_type"]),
                required=bool(row["required"]),
                config=stored_config(row["config"]),
                value=saved.get(str(row["id"])),
            )
            for row in service.items(str(assignment["version_id"]))
        ],
    )


@router.put(
    "/assignments/{assignment_id}/items/{item_id}",
    response_model=SavedAnswerResponse,
)
def save_my_answer(
    assignment_id: str,
    item_id: str,
    body: SaveAnswerRequest,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> SavedAnswerResponse:
    """Save one answer.

    Idempotent: the same question saved twice updates one row rather than
    accumulating two, so a retry after a dropped connection is safe. The
    first save moves the form from "sent" to "in progress", and a form that
    has been handed in or withdrawn is a 409 rather than a silent no-op.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)

    try:
        _saved, worth_auditing = service.save_answer(
            assignment, patient.patient_id, item_id, body.value
        )
    except (AssignmentClosedError, FrozenResponseError) as exc:
        raise ConflictError(
            "This form is no longer open for changes.", {"assignment_id": assignment_id}
        ) from exc
    except LookupError as exc:
        raise NotFoundError("Question not found", {"item_id": item_id}) from exc
    except AnswerError as exc:
        # The message names the question and what to do about it, never
        # the answer — safe to hand back to whoever is reading the form.
        raise UnprocessableEntityError(str(exc), {"item_id": item_id}) from exc

    if worth_auditing:
        # Which question was answered, and not a word of the answer. One
        # row per visit rather than per save: see the service for the
        # window and the audit action for why.
        audit.log_patient_principal_action(
            action=AuditAction.PATIENT_INTAKE_DRAFT_SAVED,
            request=request,
            patient_id=patient.patient_id,
            resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
            resource_id=assignment_id,
            changes={"item_id": item_id},
        )

    # Re-read so the status and the progress are what the save left behind
    # rather than what was there before it.
    current = _own_assignment(service, assignment_id, patient.patient_id)
    return SavedAnswerResponse(
        item_id=item_id,
        saved_at=utc_now(),
        status=str(current["status"]),
        progress=_progress(service.progress(current, patient.patient_id)),
    )


@router.post(
    "/assignments/{assignment_id}/submit",
    response_model=IntakeSubmissionResponse,
)
def submit_my_assignment(
    assignment_id: str,
    request: Request,
    patient: CurrentPatient,
    service: PatientAssignments,
    measures: OutcomeMeasureService = Depends(get_intake_outcome_measure_service),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> IntakeSubmissionResponse:
    """Hand the form in.

    A 422 listing the questions still outstanding when it is not finished,
    and nothing about the form changes. A 409 when it has already been
    handed in or withdrawn — submitting twice must not mint a second
    receipt for one set of answers, and the second caller is told the form
    is closed rather than quietly given the first receipt back.

    On success the answers stop being drafts, every measure on the form is
    scored onto the chart, and the response carries the receipt.
    """
    _require_stepped_up(patient)
    assignment = _own_assignment(service, assignment_id, patient.patient_id)

    try:
        submitted, recorded = service.submit(assignment, patient.patient_id, measures)
    except IncompleteFormError as exc:
        raise UnprocessableEntityError(
            "Some questions still need an answer.", {"missing": exc.missing}
        ) from exc
    except AssignmentClosedError as exc:
        raise ConflictError(
            "This form has already been handed in.", {"assignment_id": assignment_id}
        ) from exc

    # Which measures were on the form, and not a word of what was answered.
    # The same action the fixed intake form writes, because it is the same
    # event: this patient handed their intake in.
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_INTAKE_SUBMITTED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        changes={"instruments": [m.instrument for m in recorded]},
    )

    return IntakeSubmissionResponse(
        assignment_id=assignment_id,
        version_id=str(submitted["version_id"]),
        submitted_at=submitted["submitted_at"],  # type: ignore[arg-type]
        receipt_code=str(submitted["receipt_code"]),
        measures=[
            SubmittedMeasureResponse(
                id=m.id,
                instrument=m.instrument,
                total_score=m.total_score,
                severity=m.severity,
            )
            for m in recorded
        ],
    )


def _own_assignment(
    service: IntakeAssignmentService, assignment_id: str, patient_id: str
) -> dict[str, object]:
    """The assignment iff it is the calling patient's, else 404."""
    assignment = service.get_for_patient(assignment_id, patient_id)
    if assignment is None:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})
    return assignment


# ---------------------------------------------------------------------------
# Clinician surface
# ---------------------------------------------------------------------------


@clinician_router.post(
    "/{patient_id}/intake-assignments",
    response_model=IntakeAssignmentResponse,
)
def assign_intake(
    patient_id: str,
    body: CreateAssignmentRequest,
    request: Request,
    response: Response,
    service: ClinicianAssignments,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeAssignmentResponse:
    """Ask this patient to fill in a version of a form.

    A 201 when the request is new and a 200 when one was already live for
    the same version — sending twice must not leave somebody holding two
    copies of one form, and reissuing portal access is not a second ask.
    An unpublished version is a 422: a form can only be sent frozen, so
    that the questions somebody answered stay readable as they were asked.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    try:
        assignment, created = service.assign(patient_id, body.version_id, user.id)
    except UnpublishedVersionError as exc:
        raise UnprocessableEntityError(
            "Publish this form before sending it.", {"version_id": body.version_id}
        ) from exc
    except LookupError as exc:
        raise NotFoundError("Form not found", {"version_id": body.version_id}) from exc

    if created:
        # Which version was asked for, and nothing about the questions on
        # it. The form holds nobody's answers yet.
        audit.log(
            action=AuditAction.PATIENT_INTAKE_ASSIGNED,
            user=user,
            request=request,
            resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
            resource_id=str(assignment["id"]),
            patient=patient,
            changes={"version_id": body.version_id},
        )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return _assignment_response(service, assignment, patient_id)


@clinician_router.get(
    "/{patient_id}/intake-assignments",
    response_model=list[IntakeAssignmentResponse],
)
def list_patient_intake_assignments(
    patient_id: str,
    service: ClinicianAssignments,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
) -> list[IntakeAssignmentResponse]:
    """Which forms this patient was asked for, and how far each one has got.

    No audit row, deliberately, and it is the one read on this surface
    without one. What comes back is which form was sent, when, and a count
    of questions still outstanding — no answer, no patient-authored word,
    nothing the patient wrote. The disclosure is reading what they
    answered, and that route lands with the submission surface.

    A patient with no assignments is a 200 and an empty list, not a 404:
    the chart exists, it just has nothing on this surface yet.
    """
    if patients.get(patient_id, user.id) is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})
    return [
        _assignment_response(service, row, patient_id)
        for row in service.list_for_clinician(patient_id, user.id)
    ]


@clinician_router.get(
    "/{patient_id}/intake-assignments/{assignment_id}",
    response_model=ClinicianIntakeAssignmentDetailResponse,
)
def get_patient_intake_assignment(
    patient_id: str,
    assignment_id: str,
    request: Request,
    service: ClinicianAssignments,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> ClinicianIntakeAssignmentDetailResponse:
    """What this patient answered, question by question.

    This is the disclosure the list route deliberately is not. What comes
    back is the patient's own words — why they came, anything they said the
    chart has wrong about them, every answer they gave — so reading it goes
    on the record the way opening a conversation does. The entry carries how
    many answers were disclosed and not one of them.

    An assignment id belonging to another patient's chart is a 404, so the
    path cannot be used to find out whose form an id names.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    assignment = service.get_for_clinician(assignment_id, user.id)
    if assignment is None or str(assignment["patient_id"]) != patient_id:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})

    saved = service.answers_for_clinician(assignment_id, user.id)
    base = _assignment_response(service, assignment, patient_id)

    audit.log(
        action=AuditAction.PATIENT_INTAKE_SUBMISSION_VIEWED,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"count": len(saved)},
    )

    return ClinicianIntakeAssignmentDetailResponse(
        **base.model_dump(),
        patient_id=patient_id,
        items=[
            ClinicianIntakeAnswerResponse(
                id=str(row["id"]),
                key=str(row["key"]),
                position=int(row["position"]),  # type: ignore[call-overload]
                item_type=str(row["item_type"]),
                required=bool(row["required"]),
                config=stored_config(row["config"]),
                value=saved.get(str(row["id"])),
            )
            for row in service.items(str(assignment["version_id"]))
        ],
    )


@clinician_router.post(
    "/{patient_id}/intake-assignments/{assignment_id}/withdraw",
    response_model=IntakeAssignmentResponse,
)
def withdraw_intake_assignment(
    patient_id: str,
    assignment_id: str,
    request: Request,
    service: ClinicianAssignments,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeAssignmentResponse:
    """Stop asking for this form.

    Never a delete: whatever the patient already answered stays readable,
    and the row records that the practice stopped asking. A later save from
    the portal is refused with a 409.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    withdrawn = service.withdraw(assignment_id, user.id)
    if withdrawn is None or str(withdrawn["patient_id"]) != patient_id:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})

    audit.log(
        action=AuditAction.PATIENT_INTAKE_WITHDRAWN,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"version_id": str(withdrawn["version_id"])},
    )
    return _assignment_response(service, withdrawn, patient_id)


__all__ = [
    "clinician_router",
    "get_clinician_intake_assignment_service",
    "get_clinician_patient_repository",
    "get_patient_intake_assignment_service",
    "router",
]
