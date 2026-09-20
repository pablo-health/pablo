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

**The file says when it was taken, and in whose day.** Two exports of one
form differ in that one line and nowhere else, which is what lets a practice
check the copy they were sent against the copy they hold. It is there
because a chart copy with no date on it is a document nobody can place, and
it is written in the practice's own timezone — the same one the calendar and
the claims work in — with the zone named, like every other moment on the
page. The clock comes through a dependency so a test can fix it.
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
from ..repositories import (
    UserRepository,
    get_intake_document_repository,
    get_patient_intake_artifact_repository,
    get_user_repository,
)
from ..services.audit_service import AuditService, get_audit_service
from ..services.patient_intake_assignment_service import IntakeAssignmentService
from ..services.patient_intake_export_service import IntakeExportService
from ..services.practice_billing_profile import load_billing_profile
from ..settings import get_settings
from ..utcnow import utc_now
from .claims import _practice_timezone
from .patient_intake_assignments import (
    assignment_response,
    get_clinician_intake_assignment_service,
    get_clinician_patient_document_repository,
    get_clinician_patient_repository,
)
from .patient_intake_review import (
    get_clinician_signature_repository,
    get_intake_review_service,
)

if TYPE_CHECKING:
    from datetime import datetime, tzinfo

    from ..repositories.intake_document import IntakeDocumentRepository
    from ..repositories.patient import PatientRepository
    from ..repositories.patient_document import PatientDocumentRepository
    from ..repositories.patient_intake_artifact import PatientIntakeArtifactRepository
    from ..repositories.patient_intake_signature import PatientIntakeSignatureRepository
    from ..services.patient_intake_review_service import IntakeReviewService

#: What the browser saves the file as. The receipt is what a practice and a
#: patient can both quote, so it is what the file is named after; a form
#: that has not been handed in has no receipt, and is named after the
#: request it answers instead.
FILENAME_PREFIX = "intake-"

#: Where an attached file is read from. The clinician document route the
#: rest of the chart already uses, so there is one download path and one
#: place that records a download.
DOCUMENT_PATH = "/api/documents/"

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


def get_clinician_intake_artifact_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientIntakeArtifactRepository:
    """The artifact repository on a tenant-scoped clinician session.

    Its clinician read asks ``has_patient_access`` before it selects, like
    every other read behind this route.
    """
    return get_patient_intake_artifact_repository()


def get_intake_export_service(
    assignments: IntakeAssignmentService = Depends(get_clinician_intake_assignment_service),
    reviews: IntakeReviewService = Depends(get_intake_review_service),
    signatures: PatientIntakeSignatureRepository = Depends(get_clinician_signature_repository),
    documents: IntakeDocumentRepository = Depends(get_clinician_intake_document_repository),
    artifacts: PatientIntakeArtifactRepository = Depends(get_clinician_intake_artifact_repository),
    files: PatientDocumentRepository = Depends(get_clinician_patient_document_repository),
) -> IntakeExportService:
    """The export reader, composed from the same stores the review uses."""
    return IntakeExportService(assignments, reviews, signatures, documents, artifacts, files)


def get_export_clock() -> datetime:
    """When this copy is being taken.

    A dependency rather than a call inside the route, so a test can pin it
    and compare two documents byte for byte. Nothing else about the file
    reads a clock.
    """
    return utc_now()


def get_practice_timezone(
    _ctx: TenantContext = Depends(get_tenant_context),
    user: User = Depends(require_baa_acceptance),
    users: UserRepository = Depends(get_user_repository),
) -> tzinfo:
    """The frame the practice's day is written in.

    The clinician's own calendar timezone, which is what the appointments,
    the claims and the statements already mean by "the practice's day" —
    read from one place so a form and an appointment on the same chart can
    never disagree about when something happened. UTC when nothing is set.
    """
    return _practice_timezone(users, user.id)


def get_document_url_base(
    request: Request, _ctx: TenantContext = Depends(get_tenant_context)
) -> str:
    """Where the links beside the attached files point.

    The deployment's configured public address when it has one, and the
    address this request arrived on otherwise — which is the one the
    reader just used, so it is the one that will work for them. Never a
    relative path: the file is read outside the product, where a path
    resolves against whatever opened it.

    Nothing on the page fetches this — see :mod:`app.intake.export` — and
    what it opens is behind the sign-in the rest of the chart is behind.
    """
    configured = get_settings().backend_base_url.strip()
    return (configured or str(request.base_url)).rstrip("/")


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
    exported_at: datetime = Depends(get_export_clock),
    timezone: tzinfo = Depends(get_practice_timezone),
    url_base: str = Depends(get_document_url_base),
    audit: AuditService = Depends(get_audit_service),
) -> Response:
    """The whole form as one file, ready to file or print.

    Every question in the order the form asks them with what it holds and
    where that came from, the answers each one replaced, the files it
    collected, the evidence behind every signature, and the log of what was
    asked for and done.

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
        exported_at=exported_at,
        timezone=timezone,
        document_url=lambda document_id: f"{url_base}{DOCUMENT_PATH}{document_id}/file",
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
    "DOCUMENT_PATH",
    "FILENAME_PREFIX",
    "clinician_router",
    "get_clinician_intake_artifact_repository",
    "get_clinician_intake_document_repository",
    "get_document_url_base",
    "get_export_clock",
    "get_intake_export_service",
    "get_practice_name",
    "get_practice_timezone",
]
