# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Reading a form that has been handed in, and answering it.

The clinician's half of the review cycle, under the ordinary chart prefix:

    GET  /api/patients/{patient_id}/intake-assignments/{id}/review
    POST /api/patients/{patient_id}/intake-assignments/{id}/request-correction
    POST /api/patients/{patient_id}/intake-assignments/{id}/accept
    POST /api/patients/{patient_id}/intake-assignments/{id}/items/{item_id}/clinician-entry

Four things shape it.

**A different principal from the portal, checked a different way.** Every
route here takes a clinician who has accepted the agreement and holds a
grant on the chart. A patient session satisfies none of it, and the patient
id is in the path precisely because the caller is not the patient.

**Reading the review is a disclosure.** It hands back what the patient
wrote, question by question, plus how many earlier answers each one
replaced. That goes on the record the way opening a conversation does, and
the entry carries how many answers were disclosed and not one of them.

**The state machine is the server's.** A correction can be asked for on a
form that was handed in and on nothing else; the same goes for accepting.
Both are ``409`` otherwise, decided by reading the row rather than by
trusting what a screen believed when it rendered.

**Nothing is edited.** A correction reopens named questions for the
patient to answer again; entering a value writes a successor beside what
was there. What the patient handed in reads back afterwards exactly as
they handed it in, which is the difference between asking for a correction
and holding a pencil.

The note a correction carries is the one free text on this surface. It goes
to the patient's screen and to nowhere else — not to the audit log, which
records that corrections were asked for and on which questions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request

from ..api_errors import ConflictError, NotFoundError, UnprocessableEntityError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..intake.answers import AnswerError, validate_answer
from ..intake.items import stored_config
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.patient_intake_assignment_api import IntakeAssignmentResponse
from ..models.patient_intake_review_api import (
    ClinicianEntryRequest,
    IntakeReviewEventResponse,
    IntakeReviewItemResponse,
    IntakeReviewResponse,
    RequestCorrectionRequest,
)
from ..portal.delivery import PortalNoticeDelivery  # noqa: TC001 — fastapi resolves it
from ..portal.factory import get_notice_delivery
from ..portal.notices import send_portal_notice
from ..repositories import (
    get_intake_packet_repository,
    get_patient_intake_assignment_repository,
    get_patient_intake_signature_repository,
)
from ..services.audit_service import AuditService, get_audit_service
from ..services.patient_intake_assignment_service import (
    IntakeAssignmentService,
    event_item_ids,
)
from ..services.patient_intake_review_service import (
    AlreadyAcceptedError,
    IntakeReviewService,
    ReviewStateError,
    UnknownItemError,
)
from .patient_intake_assignments import (
    assignment_response,
    get_clinician_intake_assignment_service,
    get_clinician_patient_repository,
    optional_str,
    signature_response,
)

if TYPE_CHECKING:
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_intake_signature import PatientIntakeSignatureRepository

#: The notice a reopened form sends. A name and a link; see
#: :mod:`app.portal.notices` for why it can be nothing more.
CORRECTION_NOTICE = "intake_correction_requested"

clinician_router = APIRouter(prefix="/api/patients", tags=["patient-intake"])

# ``AuditService`` is spelled out in the route signatures rather than
# aliased: the route-audit guardrail matches the parameter annotation by
# name.


def get_intake_review_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> IntakeReviewService:
    """The review service on a tenant-scoped clinician session.

    Depends on ``get_tenant_context`` unlike its assignment-service
    neighbour, which is shared with the portal. Nothing here is shared with
    the portal: every act on this surface is one the practice performs.
    """
    return IntakeReviewService(
        get_patient_intake_assignment_repository(),
        get_intake_packet_repository(),
    )


def get_clinician_signature_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientIntakeSignatureRepository:
    """The signature repository on a tenant-scoped session."""
    return get_patient_intake_signature_repository()


ClinicianAssignments = Annotated[
    IntakeAssignmentService, Depends(get_clinician_intake_assignment_service)
]
Reviews = Annotated[IntakeReviewService, Depends(get_intake_review_service)]


@clinician_router.get(
    "/{patient_id}/intake-assignments/{assignment_id}/review",
    response_model=IntakeReviewResponse,
)
def get_intake_review(
    patient_id: str,
    assignment_id: str,
    request: Request,
    assignments: ClinicianAssignments,
    reviews: Reviews,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    signatures: PatientIntakeSignatureRepository = Depends(get_clinician_signature_repository),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeReviewResponse:
    """The whole form as the clinician reviews it.

    Every question with what it currently holds, who put it there, and how
    many earlier answers it replaced; what has been signed; and the log of
    what has been asked for and done. Wider than the plain chart read beside
    it, which is why it is audited under an action of its own.

    Signatures come back by presence: a form with no consent document on it
    hands back an empty list rather than a section explaining its own
    absence.

    An assignment id belonging to another patient's chart is a 404, so the
    path cannot be used to find out whose form an id names.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    assignment = _own_assignment(assignments, assignment_id, patient_id, user.id)
    saved = assignments.answers_for_clinician(assignment_id, user.id)
    replaced = reviews.superseded_counts(assignment_id, user.id)
    base = assignment_response(assignments, assignment, patient_id)

    audit.log(
        action=AuditAction.INTAKE_REVIEW_VIEWED,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"count": len(saved)},
    )

    return IntakeReviewResponse(
        **base.model_dump(),
        patient_id=patient_id,
        items=[
            IntakeReviewItemResponse(
                id=str(row["id"]),
                key=str(row["key"]),
                position=int(row["position"]),  # type: ignore[call-overload]
                item_type=str(row["item_type"]),
                required=bool(row["required"]),
                label=optional_str(row.get("label")),
                help_text=optional_str(row.get("help_text")),
                config=stored_config(row["config"]),
                value=saved.get(str(row["id"])),
                provenance=_provenance(assignments, assignment_id, user.id, str(row["id"])),
                superseded_count=replaced.get(str(row["id"]), 0),
            )
            for row in assignments.items(str(assignment["version_id"]))
        ],
        signatures=[
            signature_response(row)
            for row in signatures.list_live_for_assignment(assignment_id, patient_id)
        ],
        events=[_event_response(row) for row in reviews.events(assignment_id, user.id)],
    )


@clinician_router.post(
    "/{patient_id}/intake-assignments/{assignment_id}/request-correction",
    response_model=IntakeAssignmentResponse,
)
def request_intake_correction(
    patient_id: str,
    assignment_id: str,
    body: RequestCorrectionRequest,
    request: Request,
    assignments: ClinicianAssignments,
    reviews: Reviews,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    notices: PortalNoticeDelivery = Depends(get_notice_delivery),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeAssignmentResponse:
    """Send named questions back to the patient, with a note they will read.

    ``409`` when the form was not handed in — a form still being filled in
    is already the patient's, and one that has been accepted is closed.
    ``422`` when a named question is not on the form, which is what stops a
    reopened form pointing at ids no screen will ever show.

    On success the patient is told there is something waiting, if the
    deployment has wired a channel and the chart has an address. The
    message is a link to the practice's portal page and the name of the
    notice — never the note, never which form, never a question. It is
    best effort by design: the request has already been recorded, and a
    mail server being down is not a reason to lose it.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    assignment = _own_assignment(assignments, assignment_id, patient_id, user.id)
    try:
        moved, event = reviews.request_correction(
            assignment, user.id, item_ids=body.item_ids, note=body.note
        )
    except ReviewStateError as exc:
        raise ConflictError(
            "This form is not waiting to be reviewed.", {"assignment_id": assignment_id}
        ) from exc
    except UnknownItemError as exc:
        raise UnprocessableEntityError(
            "That question is not on this form.", {"item_id": str(exc)}
        ) from exc

    # Which questions were reopened, and not the note that was typed. The
    # note is the practice's own words on a row the patient reads; a second
    # copy in the compliance log answers no question it helps.
    audit.log(
        action=AuditAction.INTAKE_CORRECTION_REQUESTED,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"item_ids": event_item_ids(event)},
    )

    send_portal_notice(
        notices,
        notice=CORRECTION_NOTICE,
        to_email=patient.email,
        from_clinician_email=user.email,
    )
    return assignment_response(assignments, moved, patient_id)


@clinician_router.post(
    "/{patient_id}/intake-assignments/{assignment_id}/accept",
    response_model=IntakeAssignmentResponse,
)
def accept_intake_assignment(
    patient_id: str,
    assignment_id: str,
    request: Request,
    assignments: ClinicianAssignments,
    reviews: Reviews,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeAssignmentResponse:
    """Close a reviewed form off.

    ``409`` when the form was not handed in. A form that was reopened and
    sent back is at "handed in" again, so it is accepted by the same rule
    rather than a second one.

    Accepting takes the form out of the live set, which is what lets the
    same version be sent to the same patient again later.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    assignment = _own_assignment(assignments, assignment_id, patient_id, user.id)
    try:
        moved, _event = reviews.accept(assignment, user.id)
    except ReviewStateError as exc:
        raise ConflictError(
            "This form is not waiting to be reviewed.", {"assignment_id": assignment_id}
        ) from exc

    audit.log(
        action=AuditAction.INTAKE_ACCEPTED,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"version_id": str(moved["version_id"])},
    )
    return assignment_response(assignments, moved, patient_id)


@clinician_router.post(
    "/{patient_id}/intake-assignments/{assignment_id}/items/{item_id}/clinician-entry",
    response_model=IntakeAssignmentResponse,
)
def enter_intake_answer_for_patient(
    patient_id: str,
    assignment_id: str,
    item_id: str,
    body: ClinicianEntryRequest,
    request: Request,
    assignments: ClinicianAssignments,
    reviews: Reviews,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    audit: AuditService = Depends(get_audit_service),
) -> IntakeAssignmentResponse:
    """Write down what the patient said, for a form filled in in the room.

    The answer goes through the same validator the portal's save does, so
    the two surfaces cannot disagree about what fits a question. It lands
    as a successor: whatever was there keeps its value and gains a pointer
    to this row, so "the patient wrote this" and "we wrote this down" stay
    separable afterwards.

    ``409`` when the form has been accepted or withdrawn — both are over,
    and an answer written into either is a fact recorded under a request
    that has ended. ``422`` when the value does not fit the question, or
    the question is one whose answer is a record rather than a value: a
    consent document is signed by the person it binds, and no clinician
    entry can stand in for that.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    assignment = _own_assignment(assignments, assignment_id, patient_id, user.id)
    _validate_entry(assignments, assignment, item_id, body.value)

    try:
        _stored, _event = reviews.enter_for_patient(
            assignment, user.id, item_id=item_id, value=body.value
        )
    except AlreadyAcceptedError as exc:
        raise ConflictError(
            "This form is no longer open for changes.", {"assignment_id": assignment_id}
        ) from exc
    except UnknownItemError as exc:
        raise NotFoundError("Question not found", {"item_id": item_id}) from exc

    # Which question a clinician answered on a patient's behalf. Never the
    # value: it is a clinical answer and it lives on the row.
    audit.log(
        action=AuditAction.INTAKE_CLINICIAN_ENTRY,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"item_id": item_id},
    )

    current = _own_assignment(assignments, assignment_id, patient_id, user.id)
    return assignment_response(assignments, current, patient_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _own_assignment(
    service: IntakeAssignmentService,
    assignment_id: str,
    patient_id: str,
    user_id: str,
) -> dict[str, object]:
    """The assignment iff it is on this patient's chart, else 404."""
    assignment = service.get_for_clinician(assignment_id, user_id)
    if assignment is None or str(assignment["patient_id"]) != patient_id:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})
    return assignment


def _validate_entry(
    service: IntakeAssignmentService,
    assignment: dict[str, object],
    item_id: str,
    value: dict[str, object],
) -> None:
    """Refuse a value that would not be accepted from the portal.

    The same two checks the save route makes, in the same order: a question
    whose configuration no longer parses cannot be answered at all, a
    consent document is signed rather than answered, and anything else goes
    through :func:`~app.intake.answers.validate_answer`.
    """
    from ..intake.answers import SIGNED_ITEM_TYPES

    row = next(
        (
            item
            for item in service.items(str(assignment["version_id"]))
            if str(item["id"]) == item_id
        ),
        None,
    )
    if row is None:
        raise NotFoundError("Question not found", {"item_id": item_id})

    config = service.config_for(row)
    if config is None:
        raise UnprocessableEntityError(
            "This question cannot be answered as it is set up.", {"item_id": item_id}
        )
    if config.item_type in SIGNED_ITEM_TYPES:
        raise UnprocessableEntityError(
            "This document is signed by the person it binds.", {"item_id": item_id}
        )
    try:
        validate_answer(config, value)
    except AnswerError as exc:
        raise UnprocessableEntityError(str(exc), {"item_id": item_id}) from exc


def _provenance(
    service: IntakeAssignmentService, assignment_id: str, user_id: str, item_id: str
) -> str | None:
    """Where this question's current answer came from, or ``None`` if unanswered."""
    row = service.live_response_for_clinician(assignment_id, user_id, item_id)
    return None if row is None else optional_str(row.get("provenance"))


def _event_response(row: dict[str, object]) -> IntakeReviewEventResponse:
    return IntakeReviewEventResponse(
        id=str(row["id"]),
        kind=str(row["kind"]),
        item_ids=event_item_ids(row),
        note_to_patient=optional_str(row.get("note_to_patient")),
        created_by=optional_str(row.get("created_by")),
        created_at=row["created_at"],  # type: ignore[arg-type]
    )


__all__ = [
    "CORRECTION_NOTICE",
    "clinician_router",
    "get_clinician_signature_repository",
    "get_intake_review_service",
]
