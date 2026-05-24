# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient document API routes (THERAPY-ak6m.2).

Endpoints:

  POST   /api/patients/{patient_id}/documents/init   -> signed PUT URL
  POST   /api/documents/{document_id}/finalize       -> verify + extract
  GET    /api/patients/{patient_id}/documents        -> list
  GET    /api/documents/{document_id}                -> metadata
  GET    /api/documents/{document_id}/file           -> 302 to signed GET URL
  DELETE /api/documents/{document_id}                -> soft delete

The signed-URL flow (THERAPY-ak6m.2's design departure from a
multipart server-proxy) keeps Cloud Run bandwidth and memory flat at
arbitrary client count — uploads go browser→GCS directly. Backend
re-verifies size and mime type at finalize time as defense-in-depth.

Access model: per CLAUDE.md guardrail #1, every endpoint that touches
patient data injects ``audit: AuditService`` and emits a matching
``PATIENT_DOCUMENT_*`` event. Tenant scoping is provided by the
existing ``get_tenant_context`` Depends chain; RLS at the DB level is
the backstop in case an app-layer filter is dropped.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..api_errors import (
    BadRequestError,
    NotFoundError,
    ServerError,
    UnprocessableEntityError,
)
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..models import AuditAction, DocumentCategory, PatientDocument, User
from ..repositories import (
    PatientDocumentRepository,
    PatientRepository,
)
from ..repositories import (
    get_patient_document_repository as _patient_document_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..services import (
    AuditService,
    DocumentsBucketNotConfiguredError,
    FileTooLargeError,
    PatientDocumentError,
    PatientDocumentsService,
    UnsupportedMimeTypeError,
    UploadNotCompleteError,
    get_audit_service,
)
from ..settings import Settings, get_settings

logger = logging.getLogger(__name__)


def _read_action_for(category: DocumentCategory) -> AuditAction:
    """Pick the VIEWED audit action for a document's category.

    Chart reads emit the regular ``PATIENT_DOCUMENT_VIEWED`` action.
    Restricted-category reads emit ``PATIENT_DOCUMENT_VIEWED_RESTRICTED``
    so compliance reports can filter sensitive-document access without
    parsing the changes payload. Category itself still goes in the
    payload for the specific value (therapist_private vs.
    psychotherapy_notes).
    """
    if category.is_restricted:
        return AuditAction.PATIENT_DOCUMENT_VIEWED_RESTRICTED
    return AuditAction.PATIENT_DOCUMENT_VIEWED


def _download_action_for(category: DocumentCategory) -> AuditAction:
    """Pick the DOWNLOADED audit action for a document's category.

    Same split as :func:`_read_action_for`.
    """
    if category.is_restricted:
        return AuditAction.PATIENT_DOCUMENT_DOWNLOADED_RESTRICTED
    return AuditAction.PATIENT_DOCUMENT_DOWNLOADED


# Two separate router prefixes because the spec splits the surface:
# - "/api/patients/{patient_id}/documents" for patient-scoped reads/inits
# - "/api/documents/{document_id}"        for document-scoped ops
patient_documents_router = APIRouter(prefix="/api/patients", tags=["patient-documents"])
documents_router = APIRouter(prefix="/api/documents", tags=["patient-documents"])


# --- Dependencies ----------------------------------------------------


def get_patient_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientRepository:
    return _patient_repo_factory()


def get_patient_document_repository(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> PatientDocumentRepository:
    return _patient_document_repo_factory()


def get_patient_documents_service(
    ctx: TenantContext = Depends(get_tenant_context),
    repo: PatientDocumentRepository = Depends(get_patient_document_repository),
    settings: Settings = Depends(get_settings),
) -> PatientDocumentsService:
    """Construct the per-request service.

    Tenant id is carried into the service so the GCS object name
    inherits the per-tenant prefix. When tenant context is unavailable
    (single-tenant deploys) the service falls back to a fixed prefix.

    The OCR client is constructed unconditionally; its own
    ``is_configured`` check makes it a no-op when the deployment hasn't
    set a processor id (local dev, OSS demo). Construction is cheap —
    the underlying Google client is built lazily on first ``extract``.
    """
    from ..services.document_ai_ocr import DocumentAiOcrClient

    return PatientDocumentsService(
        repo=repo,
        settings=settings,
        tenant_id=ctx.practice_id,
        ocr_client=DocumentAiOcrClient(settings=settings),
    )


# --- Pydantic schemas -----------------------------------------------


class InitUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0)
    # Default 'chart' = doc follows patient access (co-treaters can
    # see it). 'therapist_private' / 'psychotherapy_notes' restrict
    # to uploader and feed into the release-of-records filter
    # later. See DocumentCategory docstring for the HIPAA boundary.
    # Immutable after init.
    category: DocumentCategory = DocumentCategory.CHART


class InitUploadResponse(BaseModel):
    document_id: str
    upload_url: str
    required_content_type: str
    max_bytes: int

    # Surfaced so the client can ergonomically attach the same header
    # the URL was signed against. Constant for v1; calling it out as a
    # response field keeps the contract self-describing.
    required_size_header: str = "x-goog-content-length-range"


class PatientDocumentResponse(BaseModel):
    id: str
    patient_id: str
    filename: str
    mime_type: str
    size_bytes: int
    created_at: str
    finalized_at: str | None = None
    category: DocumentCategory = DocumentCategory.CHART
    # extracted_text is present only on metadata fetch (GET /api/
    # documents/{id}); the list endpoint omits it to keep response
    # bodies small.
    extracted_text: str | None = None
    # Convenience flag for the UI's "OCR not yet supported" badge.
    text_extraction_failed: bool = False

    @classmethod
    def from_document(
        cls,
        document: PatientDocument,
        *,
        include_extracted_text: bool = False,
    ) -> PatientDocumentResponse:
        return cls(
            id=document.id,
            patient_id=document.patient_id,
            filename=document.filename,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            created_at=document.created_at.isoformat(),
            finalized_at=(document.finalized_at.isoformat() if document.finalized_at else None),
            category=document.category,
            extracted_text=(document.extracted_text if include_extracted_text else None),
            text_extraction_failed=(
                document.finalized_at is not None and document.extracted_text is None
            ),
        )


class PatientDocumentListResponse(BaseModel):
    data: list[PatientDocumentResponse]
    total: int


class DeleteDocumentResponse(BaseModel):
    message: str


# --- Routes ----------------------------------------------------------


@patient_documents_router.post(
    "/{patient_id}/documents/init",
    status_code=status.HTTP_201_CREATED,
)
def init_document_upload(
    patient_id: str,
    body: InitUploadRequest,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> InitUploadResponse:
    """Mint a signed PUT URL the browser uses to upload directly to GCS.

    The placeholder row is inserted with ``finalized_at=NULL`` so it
    won't surface in list reads until the client posts back to the
    finalize endpoint.
    """
    patient = patient_repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    try:
        result = service.init_upload(
            patient_id=patient_id,
            user_id=user.id,
            filename=body.filename,
            mime_type=body.mime_type,
            size_bytes=body.size_bytes,
            category=body.category,
        )
    except UnsupportedMimeTypeError as exc:
        raise UnprocessableEntityError(
            "Unsupported document type",
            {"mime_type": exc.mime_type},
            code="UNSUPPORTED_MIME_TYPE",
        ) from exc
    except FileTooLargeError as exc:
        raise BadRequestError(
            "File too large",
            {"max_bytes": exc.max_bytes, "size_bytes": exc.size_bytes},
            code="FILE_TOO_LARGE",
        ) from exc
    except DocumentsBucketNotConfiguredError as exc:
        raise ServerError(
            "Patient document uploads are not configured on this deployment",
            code="DOCUMENTS_NOT_CONFIGURED",
        ) from exc

    audit.log_patient_document_action(
        AuditAction.PATIENT_DOCUMENT_UPLOAD_INITIATED,
        user,
        http_request,
        document_id=result.document.id,
        patient_id=patient_id,
        mime_type=body.mime_type,
        size_bytes=body.size_bytes,
        category=body.category.value,
    )

    return InitUploadResponse(
        document_id=result.document.id,
        upload_url=result.upload_url,
        required_content_type=result.required_content_type,
        max_bytes=result.max_bytes,
    )


@documents_router.post("/{document_id}/finalize")
def finalize_document_upload(
    document_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> PatientDocumentResponse:
    """Verify the GCS object and run PyMuPDF text extraction.

    Idempotent: re-calling finalize on an already-finalized row is a
    no-op (returns the same row) so a client retry after a flaky
    network doesn't create dupes or re-extract.
    """
    try:
        document = service.finalize_upload(document_id=document_id, user_id=user.id)
    except UploadNotCompleteError as exc:
        raise BadRequestError(
            "Upload not complete — GCS object missing",
            {"document_id": document_id},
            code="UPLOAD_NOT_COMPLETE",
        ) from exc
    except FileTooLargeError as exc:
        raise BadRequestError(
            "Uploaded file exceeds the size limit",
            {"max_bytes": exc.max_bytes, "size_bytes": exc.size_bytes},
            code="FILE_TOO_LARGE",
        ) from exc
    except UnsupportedMimeTypeError as exc:
        raise UnprocessableEntityError(
            "Uploaded file has an unsupported content type",
            {"mime_type": exc.mime_type},
            code="UNSUPPORTED_MIME_TYPE",
        ) from exc
    except DocumentsBucketNotConfiguredError as exc:
        raise ServerError(
            "Patient document uploads are not configured on this deployment",
            code="DOCUMENTS_NOT_CONFIGURED",
        ) from exc
    except PatientDocumentError as exc:
        raise NotFoundError("Document not found", {"document_id": document_id}) from exc

    audit.log_patient_document_action(
        AuditAction.PATIENT_DOCUMENT_UPLOADED,
        user,
        http_request,
        document_id=document.id,
        patient_id=document.patient_id,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        category=document.category.value,
    )
    # OCR audit: emitted whenever the service attempted the Document AI
    # fallback (success OR soft failure). PyMuPDF-only finalizes leave
    # extracted_via in {None, "pymupdf"} and skip this row entirely.
    if document.extracted_via in ("document_ai", "unavailable"):
        audit.log_patient_document_ocr(
            user,
            http_request,
            document_id=document.id,
            patient_id=document.patient_id,
            processor="document_ai",
            outcome="success" if document.extracted_via == "document_ai" else "unavailable",
            metadata=document.extraction_metadata,
        )
    return PatientDocumentResponse.from_document(document)


@patient_documents_router.get("/{patient_id}/documents")
def list_patient_documents(
    patient_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    patient_repo: PatientRepository = Depends(get_patient_repository),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> PatientDocumentListResponse:
    """List the caller's documents for a patient, newest first."""
    patient = patient_repo.get(patient_id, user.id)
    if patient is None:
        raise NotFoundError("Patient not found", {"patient_id": patient_id})

    documents = service.list_for_patient(patient_id, user.id)
    audit.log_patient_action(AuditAction.PATIENT_VIEWED, user, http_request, patient)
    return PatientDocumentListResponse(
        data=[PatientDocumentResponse.from_document(d) for d in documents],
        total=len(documents),
    )


@documents_router.get("/{document_id}")
def get_document(
    document_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> PatientDocumentResponse:
    """Fetch a single document's metadata + extracted text.

    The extracted text is included so the eventual ``ak6m.4`` visit-
    code suggester can pull it without a second round-trip when the
    chat bundler asks for the document body.
    """
    document = service.get(document_id, user.id)
    if document is None:
        raise NotFoundError("Document not found", {"document_id": document_id})

    audit.log_patient_document_action(
        _read_action_for(document.category),
        user,
        http_request,
        document_id=document.id,
        patient_id=document.patient_id,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        category=document.category.value,
    )
    return PatientDocumentResponse.from_document(document, include_extracted_text=True)


@documents_router.get(
    "/{document_id}/file",
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
)
def download_document_file(
    document_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> RedirectResponse:
    """302 to a short-lived signed GET URL for the GCS object.

    Audit emission happens BEFORE the redirect so a dropped connection
    after the URL is minted still leaves a record of the access. The
    URL itself is short-lived (5 min default) so it expires before any
    log harvesting that might surface it.
    """
    try:
        result = service.signed_download_url(document_id, user.id)
    except DocumentsBucketNotConfiguredError as exc:
        raise ServerError(
            "Patient document uploads are not configured on this deployment",
            code="DOCUMENTS_NOT_CONFIGURED",
        ) from exc
    if result is None:
        raise NotFoundError("Document not found", {"document_id": document_id})

    document, signed_url = result
    audit.log_patient_document_action(
        _download_action_for(document.category),
        user,
        http_request,
        document_id=document.id,
        patient_id=document.patient_id,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        category=document.category.value,
    )
    return RedirectResponse(url=signed_url, status_code=status.HTTP_302_FOUND)


@documents_router.delete("/{document_id}")
def delete_document(
    document_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> DeleteDocumentResponse:
    """Soft-delete the document row. GCS-object cleanup is ak6m.2.1."""
    document = service.soft_delete(document_id, user.id)
    if document is None:
        raise NotFoundError("Document not found", {"document_id": document_id})

    audit.log_patient_document_action(
        AuditAction.PATIENT_DOCUMENT_DELETED,
        user,
        http_request,
        document_id=document.id,
        patient_id=document.patient_id,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        category=document.category.value,
    )
    return DeleteDocumentResponse(message="Document deleted")
