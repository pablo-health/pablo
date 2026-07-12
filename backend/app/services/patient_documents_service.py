# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Patient document upload service (THERAPY-ak6m.2).

Two-phase signed-URL upload + PyMuPDF text extraction. The service
layer owns:

* Mime/size validation at init time so an obviously-bad request fails
  before a storage object is reserved.
* Defense-in-depth re-validation at finalize time (the signed URL
  already enforces size+content-type at GCS, but we cross-check the
  blob metadata in case the constraint was tampered or GCS behavior
  shifts).
* PyMuPDF text extraction — synchronous on finalize. Native-text PDFs
  return their text body; scanned PDFs (PyMuPDF returns <100 chars)
  store ``extracted_text=NULL`` and ak6m.2.3 will OCR them.

Audit emission lives at the route layer (it needs the FastAPI Request
for IP/UA) — service raises domain errors that the route translates
into 4xx and decides whether to audit the failure.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..models import DocumentCategory, PatientDocument
from ..repositories.patient_document import FinalizedExtraction
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories import PatientDocumentRepository
    from ..settings import Settings
    from .document_ai_ocr import DocumentAiOcrClient
    from .file_storage import FileStorageProvider, UploadTarget

logger = logging.getLogger(__name__)

# Whitelist of mime types accepted for patient documents. PDF is the
# primary surface (the PMHNP pilot use case); PNG/JPEG cover scanned
# pages and photos clinicians upload from a phone. Anything else
# (.docx, .exe, .zip, ...) is rejected at init time — we do NOT want
# the chat bundler downstream having to defend against arbitrary
# binaries.
ALLOWED_MIME_TYPES: frozenset[str] = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
)

# PyMuPDF threshold below which we treat the result as a scanned PDF.
# 100 chars is well below any meaningful body of text and well above
# the 1-2 char artifacts PyMuPDF sometimes returns for image-only
# pages.
_SCANNED_PDF_TEXT_THRESHOLD = 100

# Documents shorter than this don't get a stored summary — the full body
# already fits comfortably in the chat bundle, so a summary would be
# redundant. The chat bundler only falls back to the summary when a doc
# body exceeds its render cap, which a sub-2k-char doc never will.
_SUMMARY_MIN_TEXT_CHARS = 2_000

# Length of the deterministic head-excerpt summary. We deliberately use a
# cheap, dependency-free first-N-chars excerpt rather than an inline LLM
# call: the finalize path is synchronous and idempotent (an early return
# on re-finalize means a failed inline LLM summary would leave the doc
# finalized-without-summary and no retry path), and an LLM call would add
# per-document cost + 1-5s of blocking latency to every upload. The
# excerpt is stored under ``extraction_metadata['summary']`` so the chat
# bundler's render-cap fallback has a faithful head of the document to
# show; a higher-fidelity summarizer can replace this producer later
# without changing the consumer contract.
_SUMMARY_EXCERPT_CHARS = 400


class PatientDocumentError(Exception):
    """Base class for patient-document service errors."""


class UnsupportedMimeTypeError(PatientDocumentError):
    def __init__(self, mime_type: str) -> None:
        super().__init__(f"Unsupported mime type: {mime_type!r}")
        self.mime_type = mime_type


class FileTooLargeError(PatientDocumentError):
    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        super().__init__(f"File too large: {size_bytes} bytes (max {max_bytes} bytes)")
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class DocumentsBucketNotConfiguredError(PatientDocumentError):
    """Raised when patient_documents_gcs_bucket is unset.

    Surface returns 503 so self-hosters who haven't provisioned a
    bucket get a clear configuration message instead of an opaque
    500 from the GCS SDK.
    """


class UploadNotCompleteError(PatientDocumentError):
    """Finalize was called but the storage object isn't there yet."""


@dataclass(frozen=True)
class InitUploadResult:
    document: PatientDocument
    upload: UploadTarget
    required_content_type: str
    max_bytes: int


class PatientDocumentsService:
    """Orchestrates signed-URL upload, finalize/extraction, and read paths."""

    def __init__(
        self,
        *,
        repo: PatientDocumentRepository,
        settings: Settings,
        storage: FileStorageProvider | None = None,
        tenant_id: str | None = None,
        ocr_client: DocumentAiOcrClient | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._storage_provider = storage
        self._tenant_id = tenant_id
        # None = OCR fallback disabled. When set, the client's own
        # is_configured check short-circuits if no processor id.
        self._ocr = ocr_client

    # --- storage plumbing --------------------------------------------

    def _storage(self) -> FileStorageProvider:
        """Lazy provider construction. Tests inject a fake provider."""
        if self._storage_provider is None:
            from .file_storage import file_storage_from_settings

            self._storage_provider = file_storage_from_settings(self._settings)
        return self._storage_provider

    def _bucket(self) -> str:
        bucket = self._settings.patient_documents_gcs_bucket
        if not bucket:
            raise DocumentsBucketNotConfiguredError(
                "patient_documents_gcs_bucket is not configured"
            )
        return bucket

    def _object_name(self, document_id: str, category: DocumentCategory) -> str:
        # Layout: gs://<bucket>/<tenant>/<category>/<uuid>.
        # * Tenant in the path so a bucket-policy review can confirm
        #   tenant isolation at the storage layer too (defense in
        #   depth against any future RLS regression).
        # * Category in the path so the GCS-level access audit (e.g.
        #   `gsutil ls`, log forensics) can grep for restricted-
        #   category traffic without a DB join, and so a future
        #   bucket split (e.g. move psychotherapy_notes to a
        #   stricter-IAM bucket) is a path-prefix migration rather
        #   than a content scan.
        # When tenant_id is None (single-tenant deploys), fall back to
        # a fixed "default" prefix so the path shape stays predictable.
        prefix = self._tenant_id or "default"
        return f"{prefix}/{category.value}/{document_id}"

    # --- init / finalize ---------------------------------------------

    def init_upload(
        self,
        *,
        patient_id: str,
        user_id: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        category: DocumentCategory = DocumentCategory.CHART,
    ) -> InitUploadResult:
        """Mint a signed PUT URL + insert a placeholder row.

        Validates mime + size before any GCS round-trip so a bad
        request fails fast and never reserves a path. ``size_bytes``
        is the client-claimed size; the real check happens at
        finalize against the live blob metadata.

        ``category`` defaults to :attr:`DocumentCategory.CHART` —
        the doc is part of the patient record and visible to co-
        treating clinicians via patient_clinicians grants. Pass
        :attr:`DocumentCategory.THERAPIST_PRIVATE` or
        :attr:`DocumentCategory.PSYCHOTHERAPY_NOTES` to restrict to
        the uploader; see the enum docstring for the regulatory
        difference between those two values. Category is immutable
        after init.
        """
        if mime_type not in ALLOWED_MIME_TYPES:
            raise UnsupportedMimeTypeError(mime_type)
        max_bytes = self._settings.patient_documents_max_bytes
        if size_bytes > max_bytes:
            raise FileTooLargeError(size_bytes, max_bytes)
        if size_bytes <= 0:
            raise FileTooLargeError(size_bytes, max_bytes)

        document_id = str(uuid.uuid4())
        object_name = self._object_name(document_id, category)
        bucket = self._bucket()

        upload = self._storage().make_upload_target(
            bucket=bucket,
            object_name=object_name,
            content_type=mime_type,
            max_bytes=max_bytes,
            ttl_seconds=self._settings.patient_documents_upload_url_ttl_seconds,
        )

        document = PatientDocument(
            id=document_id,
            patient_id=patient_id,
            user_id=user_id,
            filename=filename,
            mime_type=mime_type,
            gcs_path=object_name,
            size_bytes=0,  # filled in by finalize
            category=category,
            created_at=utc_now(),
        )
        self._repo.add(document)

        return InitUploadResult(
            document=document,
            upload=upload,
            required_content_type=mime_type,
            max_bytes=max_bytes,
        )

    def finalize_upload(
        self,
        *,
        document_id: str,
        user_id: str,
    ) -> PatientDocument:
        """Verify the uploaded object and run PyMuPDF text extraction.

        Returns the updated PatientDocument with ``finalized_at`` set.
        Raises if:

        * the document doesn't exist or belongs to another user
        * the storage object isn't there yet (browser never uploaded)
        * the blob is bigger than the configured cap or carries a
          mime type outside the whitelist (signed-URL bypass attempt)
        """
        document = self._repo.get(document_id, user_id)
        if document is None:
            return _raise_not_found()
        if document.finalized_at is not None:
            return document  # idempotent: re-finalize is a no-op

        storage = self._storage()
        bucket = self._bucket()
        metadata = storage.fetch_metadata(
            bucket=bucket,
            object_name=document.gcs_path,
        )
        if metadata is None:
            raise UploadNotCompleteError("storage object not found")
        size_bytes, content_type = metadata
        max_bytes = self._settings.patient_documents_max_bytes
        if size_bytes > max_bytes:
            raise FileTooLargeError(size_bytes, max_bytes)
        if content_type and content_type not in ALLOWED_MIME_TYPES:
            raise UnsupportedMimeTypeError(content_type)

        # Text extraction is PDF-only. PNG/JPEG land in the bundle as
        # images and skip both PyMuPDF and the OCR fallback.
        extracted_text: str | None = None
        extracted_via: str | None = None
        extraction_metadata: dict[str, object] | None = None

        if document.mime_type == "application/pdf":
            raw = storage.download_bytes(
                bucket=bucket,
                object_name=document.gcs_path,
            )
            extracted_text = _extract_pdf_text(raw)
            if extracted_text is not None:
                extracted_via = "pymupdf"
            elif self._ocr is not None and self._ocr.is_configured:
                # PyMuPDF saw fewer than the scanned-doc threshold —
                # fall back to Document AI. Any failure is soft.
                ocr_result = self._ocr.extract(
                    pdf_bytes=raw,
                    mime_type=document.mime_type,
                )
                if ocr_result is not None:
                    extracted_text = ocr_result.text
                    extracted_via = "document_ai"
                    extraction_metadata = {
                        "page_count": ocr_result.page_count,
                        "avg_confidence": ocr_result.avg_confidence,
                        "low_confidence_pages": list(ocr_result.low_confidence_pages),
                        "latency_ms": ocr_result.latency_ms,
                    }
                else:
                    extracted_via = "unavailable"

        # Best-effort document summary for the chat bundler's render-cap
        # fallback. Soft: never blocks finalize (which is idempotent and
        # would otherwise strand the doc finalized-without-summary).
        extraction_metadata = _with_summary(extraction_metadata, extracted_text)

        updated = self._repo.mark_finalized(
            document_id=document_id,
            user_id=user_id,
            size_bytes=size_bytes,
            extraction=FinalizedExtraction(
                text=extracted_text,
                via=extracted_via,
                metadata=extraction_metadata,
            ),
            finalized_at=utc_now(),
        )
        if updated is None:
            return _raise_not_found()
        return updated

    # --- reads --------------------------------------------------------

    def get(self, document_id: str, user_id: str) -> PatientDocument | None:
        doc = self._repo.get(document_id, user_id)
        if doc is None or doc.finalized_at is None:
            return None
        return doc

    def list_for_patient(self, patient_id: str, user_id: str) -> list[PatientDocument]:
        return self._repo.list_for_patient(patient_id, user_id)

    def signed_download_url(
        self,
        document_id: str,
        user_id: str,
        *,
        disposition: Literal["attachment", "inline"] = "attachment",
    ) -> tuple[PatientDocument, str] | None:
        document = self.get(document_id, user_id)
        if document is None:
            return None
        # inline lets the in-app viewer render PDFs/images in-page;
        # attachment forces a download with a friendly filename.
        url = self._storage().make_download_url(
            bucket=self._bucket(),
            object_name=document.gcs_path,
            ttl_seconds=self._settings.patient_documents_download_url_ttl_seconds,
            response_disposition=(
                f'{disposition}; filename="{_sanitize_filename(document.filename)}"'
            ),
        )
        return document, url

    # --- writes -------------------------------------------------------

    def soft_delete(self, document_id: str, user_id: str) -> PatientDocument | None:
        """Tombstone the row; GCS-object cleanup is ak6m.2.1."""
        document = self._repo.get(document_id, user_id)
        if document is None:
            return None
        deleted = self._repo.soft_delete(document_id, user_id, utc_now())
        if not deleted:
            return None
        document.deleted_at = utc_now()
        return document


def _raise_not_found() -> PatientDocument:
    raise PatientDocumentError("document not found")


def _extract_pdf_text(data: bytes) -> str | None:
    """Run PyMuPDF on a PDF byte string.

    Returns the joined text if the result is meaningfully long;
    ``None`` for scanned PDFs (treated as <100 chars). PyMuPDF
    exceptions bubble — a malformed PDF that pyfitz can't open is a
    client error worth surfacing.
    """
    import fitz  # type: ignore[import-untyped]

    with fitz.open(stream=data, filetype="pdf") as doc:
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text())
    body = "".join(pages).strip()
    if len(body) < _SCANNED_PDF_TEXT_THRESHOLD:
        return None
    return body


def _with_summary(
    metadata: dict[str, object] | None,
    extracted_text: str | None,
) -> dict[str, object] | None:
    """Return ``metadata`` augmented with a ``summary`` key when warranted.

    Returns the input unchanged (possibly ``None``) when there is nothing
    to summarize, so the caller's metadata handling stays a single line.
    """
    summary = _summarize_extracted_text(extracted_text)
    if summary is None:
        return metadata
    merged = dict(metadata) if metadata is not None else {}
    merged["summary"] = summary
    return merged


def _summarize_extracted_text(extracted_text: str | None) -> str | None:
    """Produce a short, stored summary of an extracted document body.

    Returns ``None`` when there is nothing worth summarizing (no text,
    or a body short enough that the bundler will render it whole). The
    summary is a deterministic head-excerpt — cheap, never fails, never
    blocks finalize. It is PHI (derived from chart content): callers
    persist it to the DB alongside ``extracted_text``; it must never be
    logged. See ``_SUMMARY_EXCERPT_CHARS`` for the producer rationale.
    """
    if extracted_text is None:
        return None
    body = extracted_text.strip()
    if len(body) <= _SUMMARY_MIN_TEXT_CHARS:
        return None
    excerpt = " ".join(body[:_SUMMARY_EXCERPT_CHARS].split())
    return f"{excerpt} […]"


def _sanitize_filename(name: str) -> str:
    # Strip CR/LF and quotes so a malicious filename can't smuggle a
    # header break into Content-Disposition. We don't try to do full
    # MIME encoding here — the GCS signed URL handles the heavy
    # lifting and the browser receives a UTF-8 string.
    return name.replace("\r", "").replace("\n", "").replace('"', "").strip() or "document"
