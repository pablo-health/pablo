# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's own empty paperwork — uploading it, and downloading one.

Some practices still work from paper. A question that asks for a document
back can name one of these, and the portal then offers it to download
before it asks for the filled-in copy: download, complete, photograph,
send. That is the fallback rather than the path — most of what a
``document_request`` asks for is something the patient already has — but
the practices that need it have no other way to ask.

**Nothing here belongs to a patient, and that is the whole access model.**
A blank form is the practice's stationery: the same file whoever it is sent
to, holding nothing about anybody. So it is practice-level, registered
not-row-scoped, and its boundary is the tenant schema — exactly like the
forms and the consent documents it sits beside. The clinician routes carry
the practice's ordinary credential and take no patient id; the portal route
carries a patient principal and returns nothing about that patient. It is
here rather than on the clinician surface because the portal has no
clinician credential to offer, not because the file is anybody's.

**It cannot be pointed at a chart.** The portal route reads this table and
only this table, so a document id naming somebody's chart is a ``404``
rather than a download. That is a stronger guarantee than a category filter
on a shared table, and it is the reason for the separate table.

Same two-phase signed-URL upload as every other file in the system: the
browser sends to storage directly, and the row is not a form anyone can
download until finalize has checked the stored object.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field

from ..api_errors import (
    BadRequestError,
    ForbiddenError,
    NotFoundError,
    ServerError,
    UnprocessableEntityError,
)
from ..auth.patient_context import AuthStrength, PatientContext, get_patient_context
from ..auth.route_access import subscription_exempt
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..intake.items import UPLOAD_MIME_TYPES
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..repositories import get_intake_blank_form_repository
from ..services.audit_service import AuditService, get_audit_service
from ..services.file_storage import UploadTarget  # noqa: TC001 — pydantic resolves at runtime
from ..services.intake_blank_form_service import (
    BlankFormNotConfiguredError,
    BlankFormTooLargeError,
    IntakeBlankFormService,
    UnsupportedBlankFormTypeError,
    UploadNotCompleteError,
)
from ..settings import Settings, get_settings

if TYPE_CHECKING:
    from ..repositories.intake_blank_form import IntakeBlankFormRepository

router = APIRouter(prefix="/api/intake/blank-forms", tags=["intake-blank-forms"])

# The portal's read of the same rows. A separate router because the two
# surfaces share no dependency: this one is reached with a patient
# principal, which the clinician door does not accept and should not.
patient_router = APIRouter(prefix="/api/patient/intake/blank-forms", tags=["intake-blank-forms"])

CurrentPatient = Annotated[PatientContext, Depends(get_patient_context)]


def get_intake_blank_form_service(
    _ctx: TenantContext = Depends(get_tenant_context),
    settings: Settings = Depends(get_settings),
) -> IntakeBlankFormService:
    """The blank-form service on a tenant-scoped clinician session.

    The tenant context is the whole isolation story for this surface: every
    query underneath runs against one practice's schema, and no row here
    belongs to a narrower owner than the practice.
    """
    repo: IntakeBlankFormRepository = get_intake_blank_form_repository()
    return IntakeBlankFormService(repo=repo, settings=settings, tenant_id=_ctx.practice_id)


def get_patient_intake_blank_form_service(
    patient: PatientContext = Depends(get_patient_context),
    settings: Settings = Depends(get_settings),
) -> IntakeBlankFormService:
    """The same service on the patient-armed session.

    No ``get_tenant_context`` dependency: a patient principal arms its own
    schema through ``get_patient_context``, which the one route using this
    already depends on.
    """
    return IntakeBlankFormService(
        repo=get_intake_blank_form_repository(),
        settings=settings,
        tenant_id=patient.practice_schema,
    )


BlankForms = Annotated[IntakeBlankFormService, Depends(get_intake_blank_form_service)]
PatientBlankForms = Annotated[
    IntakeBlankFormService, Depends(get_patient_intake_blank_form_service)
]


def _require_stepped_up(patient: PatientContext) -> None:
    """Refuse a single-factor principal on the portal route.

    Same bar as the rest of the patient intake surface. A blank form holds
    nothing about anybody, but it is read from inside a form somebody is
    filling in, and the whole surface is held to one bar rather than to a
    judgement per route.
    """
    if patient.auth_strength is not AuthStrength.STEPPED_UP:
        raise ForbiddenError("Confirm it is you to continue.", code="STEP_UP_REQUIRED")


class InitBlankFormRequest(BaseModel):
    """What a practice says it is about to upload."""

    title: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0)


class InitBlankFormResponse(BaseModel):
    form_id: str
    upload: UploadTarget
    #: For client-side pre-flight only; the storage layer enforces.
    max_bytes: int


class BlankFormResponse(BaseModel):
    """One blank form as the editor and the portal both see it.

    No storage path and no uploader: the first is an implementation detail
    of where the file lives, and the second is a question about the
    practice's own staff that no screen reading this has.
    """

    id: str
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: str


class BlankFormDownloadUrlResponse(BaseModel):
    """A short-lived signed URL for the file itself."""

    url: str


class DeleteBlankFormResponse(BaseModel):
    message: str


def _response(row: dict[str, object]) -> BlankFormResponse:
    """A stored row as both surfaces show it.

    The repositories hand rows back as ``dict[str, object]``, so every
    column arrives untyped and is narrowed here rather than trusted — the
    same shape the other route modules in this package use.
    """
    created_at = row["created_at"]
    return BlankFormResponse(
        id=str(row["id"]),
        title=str(row["title"]),
        filename=str(row["filename"]),
        mime_type=str(row["mime_type"]),
        size_bytes=int(row["size_bytes"]),  # type: ignore[call-overload]
        created_at=created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
    )


# ---------------------------------------------------------------------------
# The practice's side
# ---------------------------------------------------------------------------


@router.post("/init", status_code=status.HTTP_201_CREATED)
def init_blank_form_upload(
    body: InitBlankFormRequest,
    service: BlankForms,
    user: User = Depends(require_baa_acceptance),
) -> InitBlankFormResponse:
    """Mint a signed URL the practice's browser uploads to directly.

    Not audited, and it is not an omission: nothing on this surface is
    anybody's protected health information. A blank form is the practice's
    own stationery, and the compliance log records disclosures.
    """
    try:
        result = service.init_upload(
            title=body.title,
            filename=body.filename,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            uploaded_by=user.id,
        )
    except UnsupportedBlankFormTypeError as exc:
        raise UnprocessableEntityError(
            "Unsupported document type",
            {"mime_type": exc.mime_type, "accepted": list(UPLOAD_MIME_TYPES)},
            code="UNSUPPORTED_MIME_TYPE",
        ) from exc
    except BlankFormTooLargeError as exc:
        raise BadRequestError(
            "File too large",
            {"max_bytes": exc.max_bytes, "size_bytes": exc.size_bytes},
            code="FILE_TOO_LARGE",
        ) from exc
    except BlankFormNotConfiguredError as exc:
        raise ServerError(
            "Document uploads are not configured on this deployment",
            code="DOCUMENTS_NOT_CONFIGURED",
        ) from exc
    return InitBlankFormResponse(
        form_id=result.form_id, upload=result.upload, max_bytes=result.max_bytes
    )


@router.post("/{form_id}/finalize")
def finalize_blank_form_upload(
    form_id: str,
    service: BlankForms,
    _user: User = Depends(require_baa_acceptance),
) -> BlankFormResponse:
    """Confirm the upload finished, and make the form offerable.

    Idempotent: calling it again on a finished upload returns the same row.
    Until it succeeds the form appears in no list and no question can offer
    it.
    """
    try:
        row = service.finalize_upload(form_id)
    except UploadNotCompleteError as exc:
        raise BadRequestError(
            "That upload has not finished.", {"form_id": form_id}, code="UPLOAD_NOT_COMPLETE"
        ) from exc
    except BlankFormNotConfiguredError as exc:
        raise ServerError(
            "Document uploads are not configured on this deployment",
            code="DOCUMENTS_NOT_CONFIGURED",
        ) from exc
    if row is None:
        raise NotFoundError("Form not found", {"form_id": form_id})
    return _response(row)


@router.get("")
def list_blank_forms(
    service: BlankForms,
    _user: User = Depends(require_baa_acceptance),
) -> list[BlankFormResponse]:
    """Every blank form the practice can offer, newest first."""
    return [_response(row) for row in service.list_all()]


@router.get("/{form_id}/file")
def download_blank_form(
    form_id: str,
    service: BlankForms,
    disposition: Literal["attachment", "inline"] = Query("attachment"),
    _user: User = Depends(require_baa_acceptance),
) -> BlankFormDownloadUrlResponse:
    """A short-lived signed URL, so the practice can check what it uploaded."""
    url = _signed_url_or_404(service, form_id, disposition)
    return BlankFormDownloadUrlResponse(url=url)


@router.delete("/{form_id}")
def delete_blank_form(
    form_id: str,
    service: BlankForms,
    _user: User = Depends(require_baa_acceptance),
) -> DeleteBlankFormResponse:
    """Stop offering a form. Never a hard delete.

    A published question may still name it, and an item pointing at a
    tombstone shows no download rather than a broken one.
    """
    if not service.soft_delete(form_id):
        raise NotFoundError("Form not found", {"form_id": form_id})
    return DeleteBlankFormResponse(message="Form removed")


# ---------------------------------------------------------------------------
# The portal's side
# ---------------------------------------------------------------------------


@patient_router.get("/{form_id}/file")
def download_blank_form_for_patient(
    form_id: str,
    request: Request,
    patient: CurrentPatient,
    service: PatientBlankForms,
    disposition: Literal["attachment", "inline"] = Query("attachment"),
    audit: AuditService = Depends(get_audit_service),
    _: None = Depends(subscription_exempt),
) -> BlankFormDownloadUrlResponse:
    """The blank form a question offered, for the patient to print.

    Reads this table and only this table, so an id that names a chart
    document is a ``404`` — the route cannot be pointed at somebody's
    record even in principle.

    Audited, unlike the practice's own download beside it, and for the
    reason every minted URL on the patient surface is: the signature
    authorizes the fetch on its own, it leaves the request, and it works
    without a bearer token. The entry names the form and nothing about the
    patient beyond who asked.

    Exempt from the subscription gate like every patient route: a patient
    does not hold the practice's subscription, and a form they were asked
    to fill in should not fail for a billing state they cannot see.
    """
    _require_stepped_up(patient)
    url = _signed_url_or_404(service, form_id, disposition)
    audit.log_patient_principal_action(
        action=AuditAction.PATIENT_DOCUMENT_DOWNLOADED,
        request=request,
        patient_id=patient.patient_id,
        resource_type=ResourceType.INTAKE_DOCUMENT,
        resource_id=form_id,
        session_id=patient.session_id,
        changes={"kind": "blank_form", "disposition": disposition},
    )
    return BlankFormDownloadUrlResponse(url=url)


def _signed_url_or_404(
    service: IntakeBlankFormService,
    form_id: str,
    disposition: Literal["attachment", "inline"],
) -> str:
    """The signed URL for a finished form, or the one refusal every miss gets.

    A deleted form, an unfinished upload and an id that names something
    else all answer the same way, so nothing on either surface says what
    exists.
    """
    try:
        url = service.signed_download_url(form_id, disposition=disposition)
    except BlankFormNotConfiguredError as exc:
        raise ServerError(
            "Document uploads are not configured on this deployment",
            code="DOCUMENTS_NOT_CONFIGURED",
        ) from exc
    if url is None:
        raise NotFoundError("Form not found", {"form_id": form_id})
    return url


__all__ = [
    "BlankFormResponse",
    "get_intake_blank_form_service",
    "get_patient_intake_blank_form_service",
    "patient_router",
    "router",
]
