# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Taking a form out of the product as one file.

    GET /api/patients/{patient_id}/intake-assignments/{id}/export

One route, and everything about it follows from what the file is for. A
practice needs a chart copy: something to file, to attach to a referral, to
hand over under a release of information. So the answer is a document rather
than a payload — a single self-contained HTML file any browser opens and any
printer prints, with no library to install on the way.

**The same principal as the review beside it.** A clinician who has accepted
the agreement and holds a grant on the chart. A form on somebody else's
chart is a 404, so the path cannot be used to find out whose form an id
names, and a patient session is refused the way it is refused everywhere on
this prefix.

**Exporting is a disclosure, and a wider one than reading.** The review
screen shows the current answers; the file carries the answers that were
replaced, the evidence behind every signature, and the log — and then it
leaves, which is the part the record has to be able to answer for later. So
it is audited under an action of its own, with the assignment id and nothing
out of the document.

**The file says nothing about when it was made.** Two exports of one form
are byte-identical, which is what lets a practice check the copy they were
sent against the copy they hold. When it was produced is a fact about the
export rather than about the form, and that fact is on the audit log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, Response

from ..api_errors import NotFoundError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..db import get_db_session
from ..intake.export import render
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..repositories import get_intake_document_repository
from ..services.audit_service import AuditService, get_audit_service
from ..services.patient_intake_assignment_service import IntakeAssignmentService
from ..services.patient_intake_export_service import IntakeExportService
from ..services.practice_billing_profile import load_billing_profile
from .patient_intake_assignments import (
    assignment_response,
    get_clinician_intake_assignment_service,
    get_clinician_patient_repository,
)
from .patient_intake_review import (
    get_clinician_signature_repository,
    get_intake_review_service,
)

if TYPE_CHECKING:
    from ..repositories.intake_document import IntakeDocumentRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_intake_signature import PatientIntakeSignatureRepository
    from ..services.patient_intake_review_service import IntakeReviewService

#: What the browser saves the file as. The receipt is what a practice and a
#: patient can both quote, so it is what the file is named after; a form
#: that has not been handed in has no receipt, and is named after the
#: request it answers instead.
FILENAME_PREFIX = "intake-"

clinician_router = APIRouter(prefix="/api/patients", tags=["patient-intake"])

# ``AuditService`` is spelled out in the route signature rather than
# aliased: the route-audit guardrail matches the parameter annotation by
# name.


def get_clinician_intake_document_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> IntakeDocumentRepository:
    """The document repository on a tenant-scoped session.

    A consent document belongs to the practice rather than to a patient, so
    the tenant schema is the whole scope. What is grant-checked is the
    signature that points at it, which the caller has already been through.
    """
    return get_intake_document_repository()


def get_practice_name(_ctx: TenantContext = Depends(get_tenant_context)) -> str | None:
    """The practice's own name, as the billing profile holds it.

    Read from the profile the claims and statement surfaces already keep, so
    a practice fills its identity in once. ``None`` when nothing has been
    filled in: the document then prints no practice line, rather than a
    blank one or a guess.
    """
    legal_name = load_billing_profile(get_db_session()).get("legal_name")
    return legal_name if isinstance(legal_name, str) and legal_name.strip() else None


def get_intake_export_service(
    assignments: IntakeAssignmentService = Depends(get_clinician_intake_assignment_service),
    reviews: IntakeReviewService = Depends(get_intake_review_service),
    signatures: PatientIntakeSignatureRepository = Depends(get_clinician_signature_repository),
    documents: IntakeDocumentRepository = Depends(get_clinician_intake_document_repository),
) -> IntakeExportService:
    """The export reader, composed from the same stores the review uses."""
    return IntakeExportService(assignments, reviews, signatures, documents)


ClinicianAssignments = Annotated[
    IntakeAssignmentService, Depends(get_clinician_intake_assignment_service)
]
Exports = Annotated[IntakeExportService, Depends(get_intake_export_service)]


@clinician_router.get(
    "/{patient_id}/intake-assignments/{assignment_id}/export",
    response_class=Response,
    responses={200: {"content": {"text/html": {}}, "description": "The form as one document."}},
)
def export_intake_assignment(
    patient_id: str,
    assignment_id: str,
    request: Request,
    assignments: ClinicianAssignments,
    exports: Exports,
    user: User = Depends(require_baa_acceptance),
    patients: PatientRepository = Depends(get_clinician_patient_repository),
    practice_name: str | None = Depends(get_practice_name),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """The whole form as one file, ready to file or print.

    Every question in the order the form asks them with what it holds and
    where that came from, the answers each one replaced, the evidence behind
    every signature, and the log of what was asked for and done.

    An assignment id belonging to another patient's chart is a 404, so the
    path cannot be used to find out whose form an id names.
    """
    patient = patients.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    assignment = assignments.get_for_clinician(assignment_id, user.id)
    if assignment is None or str(assignment["patient_id"]) != patient_id:
        raise NotFoundError("Form not found", {"assignment_id": assignment_id})

    base = assignment_response(assignments, assignment, patient_id)
    document = exports.build(
        assignment,
        user.id,
        patient_name=f"{patient.first_name} {patient.last_name}".strip(),
        patient_date_of_birth=patient.date_of_birth,
        packet_name=base.packet_name,
        version=base.version,
        practice_name=practice_name,
    )

    # Which form left, and nothing that was on it. The document carries the
    # patient's own words; a second copy in the compliance log would put
    # them somewhere the log has no use for them.
    audit.log(
        action=AuditAction.INTAKE_PACKET_EXPORTED,
        user=user,
        request=request,
        resource_type=ResourceType.PATIENT_INTAKE_ASSIGNMENT,
        resource_id=assignment_id,
        patient=patient,
        changes={"version_id": str(assignment["version_id"])},
    )

    filename = f"{FILENAME_PREFIX}{base.receipt_code or assignment_id}.html"
    return Response(
        content=render(document),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = [
    "FILENAME_PREFIX",
    "clinician_router",
    "get_clinician_intake_document_repository",
    "get_intake_export_service",
    "get_practice_name",
]
