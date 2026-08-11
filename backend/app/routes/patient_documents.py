# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient document API routes (THERAPY-ak6m.2).

Endpoints:

  POST   /api/patients/{patient_id}/documents/init   -> signed PUT URL
  POST   /api/documents/{document_id}/finalize       -> verify + queue extraction (202)
  GET    /api/patients/{patient_id}/documents        -> list
  GET    /api/documents/{document_id}                -> metadata
  GET    /api/documents/{document_id}/file           -> 302 to signed GET URL
  DELETE /api/documents/{document_id}                -> soft delete
  POST   /api/internal/jobs/finalize-document        -> Cloud Tasks worker: extract

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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ..api_errors import (
    BadRequestError,
    NotFoundError,
    ServerError,
    UnprocessableEntityError,
)
from ..auth.service import (
    TenantContext,
    get_tenant_context,
    require_baa_acceptance,
    require_cloud_tasks_invoker,
)
from ..models import AuditAction, DocumentCategory, ExtractionStatus, PatientDocument, User
from ..repositories import (
    PatientDocumentRepository,
    PatientRepository,
    UserRepository,
    get_user_repository,
)
from ..repositories import (
    get_patient_document_repository as _patient_document_repo_factory,
)
from ..repositories import (
    get_patient_repository as _patient_repo_factory,
)
from ..services import (
    AuditService,
    DocumentExtractionFailedError,
    DocumentsBucketNotConfiguredError,
    FileTooLargeError,
    PatientDocumentError,
    PatientDocumentsService,
    TransientDocumentExtractionError,
    UnsupportedMimeTypeError,
    UploadNotCompleteError,
    get_audit_service,
)
from ..services.document_finalize_worker import UnknownTenantError, run_document_finalize_job
from ..services.file_storage import UploadTarget  # noqa: TC001 — pydantic resolves at runtime
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
# Unprefixed: the Cloud Tasks worker endpoint lives at /api/internal/jobs/...,
# outside both of the prefixes above (mirrors routes.sessions.router, which
# hosts /api/internal/jobs/generate-soap the same way).
internal_jobs_router = APIRouter(tags=["patient-documents"])


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
    """
    from ..services.document_ai_ocr import DocumentAiOcrClient

    return PatientDocumentsService(
        repo=repo,
        settings=settings,
        tenant_id=ctx.practice_id,
        ocr_client=DocumentAiOcrClient(settings=settings),
    )


def get_worker_patient_documents_service(
    settings: Settings = Depends(get_settings),
) -> PatientDocumentsService:
    """PatientDocumentsService for the off-request Cloud Tasks worker.

    Built from the raw repository factory — NOT the ``get_tenant_context``-
    scoped ``get_patient_document_repository`` above. The worker
    authenticates as the Cloud-Tasks service account, which has no Firebase
    user session, so it must not depend on ``get_tenant_context``.
    ``document_finalize_worker.run_document_finalize_job`` arms the
    request-scoped DB session's tenant schema (from the job's ``user_id``)
    before this service's repository does any reads or writes — same
    pattern as ``get_worker_session_service`` / ``session_generation_worker``.

    No ``tenant_id`` is passed to the service — it's only used to build a
    per-tenant GCS object-name prefix at ``init_upload`` time, which the
    worker never calls.
    """
    from ..services.document_ai_ocr import DocumentAiOcrClient

    return PatientDocumentsService(
        repo=_patient_document_repo_factory(),
        settings=settings,
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
    # Self-describing upload recipe: PUT the raw body with `headers`
    # attached (GCS), or POST multipart/form-data with `fields` ahead
    # of the file part (S3). The client executes it without knowing
    # which storage provider is configured.
    upload: UploadTarget
    # For client-side pre-flight UX only; the storage layer enforces.
    max_bytes: int


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
    # 'pending' while the finalize worker is still running; 'complete' or
    # 'failed' once it's done. The frontend polls GET /api/documents/{id}
    # while this is 'pending'.
    extraction_status: ExtractionStatus = ExtractionStatus.COMPLETE
    # Convenience flag for the UI's "OCR not yet supported" badge. True
    # only once extraction has terminally failed — a 'pending' document,
    # or a 'complete' one with no text (e.g. scanned PDF, OCR unavailable),
    # is not a failure.
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
            extraction_status=document.extraction_status,
            text_extraction_failed=(document.extraction_status == ExtractionStatus.FAILED),
        )


class PatientDocumentListResponse(BaseModel):
    data: list[PatientDocumentResponse]
    total: int


class DeleteDocumentResponse(BaseModel):
    message: str


class DocumentDownloadUrlResponse(BaseModel):
    # Short-lived signed GCS URL. The signature authorizes the object
    # fetch, so the client navigates to it directly (no bearer token) —
    # a raw <a href> to /file can't carry our Authorization header.
    url: str


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
        upload=result.upload,
        max_bytes=result.max_bytes,
    )


@documents_router.post("/{document_id}/finalize", status_code=status.HTTP_202_ACCEPTED)
def finalize_document_upload(
    document_id: str,
    http_request: Request,
    user: User = Depends(require_baa_acceptance),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> PatientDocumentResponse:
    """Verify the GCS object and queue off-request text extraction.

    Only the cheap blob validation (existence, size, mime type) runs on
    this request thread; GCS download + PyMuPDF + Document AI run on a
    Cloud Tasks worker (``/api/internal/jobs/finalize-document``) so a
    scanned, OCR-fallback PDF never ties up the request thread for the
    worst-case ~2 minutes that path can take. Returns ``202`` with the
    document in ``extraction_status: "pending"``; poll
    ``GET /api/documents/{id}`` for ``"complete"`` / ``"failed"``.

    Idempotent: re-calling finalize on an already-finalized row is a
    no-op (returns the same row) so a client retry after a flaky
    network doesn't create dupes or re-queue the job.
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
    return PatientDocumentResponse.from_document(document)


class FinalizeDocumentJob(BaseModel):
    """Cloud Tasks payload for off-request document-finalize extraction.

    Opaque identifiers only — deliberately no tenant schema name (it can
    identify a tenant) and no filename/extracted text. The worker
    re-resolves the schema from ``user_id`` server-side.
    """

    document_id: str
    user_id: str


def _is_final_finalize_attempt(request: Request) -> bool:
    """Whether this is the last Cloud Tasks delivery for a finalize job.

    Cloud Tasks stamps ``X-CloudTasks-TaskRetryCount`` (0 on the first
    attempt). Compared against ``soap_generation_max_attempts`` — the
    finalize queue deliberately reuses ``pablo-soap-generation`` (see
    ``Settings.document_finalize_task_queue``), so its retry budget is
    that queue's, and this mirrors ``sessions._is_final_soap_attempt``.
    """
    raw = request.headers.get("X-CloudTasks-TaskRetryCount")
    try:
        retry_count = int(raw) if raw is not None else 0
    except ValueError:
        retry_count = 0
    return retry_count >= get_settings().soap_generation_max_attempts - 1


@internal_jobs_router.post("/api/internal/jobs/finalize-document", status_code=status.HTTP_200_OK)
def finalize_document_job(
    payload: FinalizeDocumentJob,
    http_request: Request,
    _invoker: None = Depends(require_cloud_tasks_invoker),
    service: PatientDocumentsService = Depends(get_worker_patient_documents_service),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """Worker: run GCS download + PyMuPDF + Document AI for a pending document.

    Invoked only by Cloud Tasks (service-account OIDC, enforced by
    ``require_cloud_tasks_invoker``). Scopes itself to the job's tenant via
    ``document_finalize_worker.run_document_finalize_job`` and runs
    extraction off the finalize request thread.

    Answers ``200`` once the job is accounted for — a success, the non-
    retryable outcomes (unknown tenant, vanished document), and a recorded
    *deterministic* extraction failure (the document is durably marked
    ``failed``). A ``200`` tells Cloud Tasks the job is done. A *transient*
    failure (e.g. a GCS hiccup) instead returns ``503`` so the queue
    retries with backoff, until the final attempt — at which point it is
    recorded as ``failed`` and answered ``200``.
    """
    try:
        document = run_document_finalize_job(
            document_id=payload.document_id,
            user_id=payload.user_id,
            document_service=service,
            transient_is_terminal=_is_final_finalize_attempt(http_request),
        )
    except UnknownTenantError:
        logger.warning(
            "finalize-document job: no active tenant for user %s — dropping (non-retryable)",
            payload.user_id,
        )
        return {"status": "unknown_tenant"}
    except PatientDocumentError:
        logger.warning(
            "finalize-document job: document %s not found — dropping (non-retryable)",
            payload.document_id,
        )
        return {"status": "not_found"}
    except TransientDocumentExtractionError:
        logger.warning(
            "finalize-document job: transient failure for document %s — retrying via queue",
            payload.document_id,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document extraction temporarily unavailable; retrying.",
        ) from None
    except DocumentExtractionFailedError:
        # Document already durably marked FAILED inside the service. Don't
        # 5xx — a deterministic (or exhausted-retries) failure must not
        # loop the queue.
        return {"status": "failed"}

    owner = user_repo.get(payload.user_id)
    if owner is not None and document.extracted_via in ("document_ai", "unavailable"):
        audit.log_patient_document_ocr(
            owner,
            http_request,
            document_id=document.id,
            patient_id=document.patient_id,
            processor="document_ai",
            outcome="success" if document.extracted_via == "document_ai" else "unavailable",
            metadata=document.extraction_metadata,
        )
    elif owner is None:
        logger.warning(
            "finalize-document job: owner %s not found for audit on document %s",
            payload.user_id,
            document.id,
        )
    return {"status": "ok"}


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


@documents_router.get("/{document_id}/file")
def download_document_file(
    document_id: str,
    http_request: Request,
    disposition: Literal["attachment", "inline"] = Query("attachment"),
    user: User = Depends(require_baa_acceptance),
    service: PatientDocumentsService = Depends(get_patient_documents_service),
    audit: AuditService = Depends(get_audit_service),
) -> DocumentDownloadUrlResponse:
    """Return a short-lived signed GET URL for the GCS object as JSON.

    The client fetches this through the authenticated API client (bearer
    token attached) and then navigates to the signed URL directly. A raw
    <a href> navigation can't carry our Authorization header, so a 302
    here would 401 before the redirect ever fired (PABLO-47h).

    ``disposition=inline`` mints a URL the in-app viewer can render in
    place (PDF/image); the default ``attachment`` forces a download with
    a friendly filename.

    Audit emission happens BEFORE the URL is returned so a dropped
    connection after the URL is minted still leaves a record of the
    access. The URL itself is short-lived (5 min default) so it expires
    before any log harvesting that might surface it.
    """
    try:
        result = service.signed_download_url(document_id, user.id, disposition=disposition)
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
    return DocumentDownloadUrlResponse(url=signed_url)


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
