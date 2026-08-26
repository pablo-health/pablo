# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Document AI OCR fallback for scanned patient PDFs (THERAPY-ak6m.2.3).

Called from ``PatientDocumentsService.finalize_upload`` when PyMuPDF
returns below the scanned-doc threshold. Every failure mode (no
config, oversized doc, API error) maps to ``None`` so a flaky OCR
call never 500s the upload — the doc just lands without
``extracted_text`` and the chat bundler skips it as it would any
other scanned PDF.

Sync only. The OCR processor's ``processDocument`` API caps around
30 pages, so we cap at the same number; larger docs are out of scope
for v1. Low confidence prefixes the body with a marker — we don't
gate, because a partial extraction beats nothing.

Project and processor id are optional settings — when unset, the
client derives them (ambient GCP project, then the processor whose
displayName matches) and caches the result for its lifetime. See
``DocumentAiOcrClient._resolve_target``.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..reliability import HTTP_REQUEST, Idempotency, RetryExhaustedError, call_with_retry

if TYPE_CHECKING:
    from ..settings import Settings

logger = logging.getLogger(__name__)

_AVG_CONFIDENCE_LOW_THRESHOLD = 0.5

_LOW_CONFIDENCE_PAGE_FRACTION_THRESHOLD = 0.25
_PAGE_CONFIDENCE_LOW_THRESHOLD = 0.5

# displayName of the processor we look for when document_ai_processor_id is
# unset. A GCP project can hold several Document AI processors (OCR, Form
# Parser, handwriting); this is the one we mean.
PABLO_OCR_PROCESSOR_NAME = "pablo-patient-doc-ocr"

# Test seam: monkeypatched to a no-op so retry tests don't sleep for real.
_retry_sleep = time.sleep

# Per-call deadline. SDK default is 300s with internal retries; 60s
# lets auth and quota failures surface in seconds.
_PROCESS_TIMEOUT_SECONDS = 60.0

_LOW_CONFIDENCE_MARKER = "[extraction had low confidence — verify before relying on details]\n\n"


class OcrUnavailableError(RuntimeError):
    """Raised when the OCR client can't be constructed (missing dep)."""


@dataclass(frozen=True)
class OcrResult:
    text: str
    page_count: int
    avg_confidence: float
    low_confidence_pages: list[int] = field(default_factory=list)
    latency_ms: int = 0


class DocumentAiOcrClient:
    """Thin wrapper around Document AI's online ``processDocument`` API.

    Construction is cheap (lazy auth at first call). Inject a fake
    underlying client via ``_client_factory`` in tests; production
    construction uses ``google.cloud.documentai`` directly.

    The GCP project and processor id are resolved lazily on first use
    and cached for the client's lifetime: project from
    ``document_ai_project_id``, else ``GOOGLE_CLOUD_PROJECT``, else
    ``google.auth.default()``; processor from
    ``document_ai_processor_id``, else the processor whose
    ``displayName`` is ``PABLO_OCR_PROCESSOR_NAME``. If resolution
    fails, that failure is cached too — discovery is attempted at
    most once per instance, success or failure.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        client_factory: Any | None = None,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: Any | None = None
        self._resolved_target: tuple[str, str] | None = None
        self._resolution_failed = False

    # --- public API ---------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """True iff the kill-switch is on.

        Project and processor are resolved lazily (and may still fail
        to resolve) — this only reflects deliberate operator intent.
        """
        return bool(self._settings.allow_document_ai_ocr)

    def extract(self, *, pdf_bytes: bytes, mime_type: str) -> OcrResult | None:
        """OCR a PDF. Returns ``None`` on any soft failure."""
        if not self.is_configured or mime_type != "application/pdf":
            return None

        prepared = self._prepare()
        if prepared is None:
            return None
        client, project, processor_id = prepared

        page_count = _count_pdf_pages(pdf_bytes)
        if page_count > self._settings.document_ai_max_pages:
            logger.info(
                "document_ai OCR skipped: page_count=%d exceeds max=%d",
                page_count,
                self._settings.document_ai_max_pages,
            )
            return None

        request = self._build_request(pdf_bytes, project=project, processor_id=processor_id)
        start = time.monotonic()
        response = _call_with_one_retry(client.process_document, request)
        latency_ms = int((time.monotonic() - start) * 1000)

        if response is None:
            return None

        return _parse_response(response, latency_ms=latency_ms)

    # --- internals ----------------------------------------------------

    def _prepare(self) -> tuple[Any, str, str] | None:
        """Build the underlying client and resolve (project, processor_id)."""
        try:
            client = self._get_client()
        except OcrUnavailableError:
            logger.warning("document_ai client unavailable; OCR skipped")
            return None

        target = self._resolve_target(client)
        if target is None:
            return None
        project, processor_id = target
        return client, project, processor_id

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            self._client = self._client_factory()
            return self._client
        try:
            from google.api_core.client_options import ClientOptions
            from google.cloud import documentai  # type: ignore[attr-defined]
        except ImportError as exc:
            raise OcrUnavailableError("google-cloud-documentai not installed") from exc

        endpoint = f"{self._settings.document_ai_location}-documentai.googleapis.com"
        self._client = documentai.DocumentProcessorServiceClient(
            client_options=ClientOptions(api_endpoint=endpoint)
        )
        return self._client

    def _build_request(self, pdf_bytes: bytes, *, project: str, processor_id: str) -> Any:
        from google.cloud import documentai  # type: ignore[attr-defined]

        processor_name = (
            f"projects/{project}"
            f"/locations/{self._settings.document_ai_location}"
            f"/processors/{processor_id}"
        )
        raw_document = documentai.RawDocument(
            content=pdf_bytes,
            mime_type="application/pdf",
        )
        return documentai.ProcessRequest(
            name=processor_name,
            raw_document=raw_document,
        )

    def _resolve_target(self, client: Any) -> tuple[str, str] | None:
        """Resolve (project, processor_id), caching success and failure alike."""
        if self._resolved_target is not None:
            return self._resolved_target
        if self._resolution_failed:
            return None

        project, project_source = self._resolve_project()
        if project is None:
            self._resolution_failed = True
            logger.warning(
                "document_ai resolution failed: could not determine a GCP project "
                "(set document_ai_project_id or GOOGLE_CLOUD_PROJECT)"
            )
            return None

        processor_id, processor_source = self._resolve_processor(client, project=project)
        if processor_id is None:
            self._resolution_failed = True
            return None

        self._resolved_target = (project, processor_id)
        logger.info(
            "document_ai resolved: project=%s (%s) location=%s processor=%s (%s)",
            project,
            project_source,
            self._settings.document_ai_location,
            processor_id,
            processor_source,
        )
        return self._resolved_target

    def _resolve_project(self) -> tuple[str | None, str]:
        s = self._settings
        if s.document_ai_project_id:
            return s.document_ai_project_id, "configured"

        env_project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if env_project:
            return env_project, "environment"

        try:
            import google.auth

            _credentials, project = google.auth.default()
        except Exception:
            logger.warning("document_ai project discovery failed: google.auth.default() raised")
            return None, "discovered"

        return (project, "discovered") if project else (None, "discovered")

    def _resolve_processor(self, client: Any, *, project: str) -> tuple[str | None, str]:
        s = self._settings
        if s.document_ai_processor_id:
            return s.document_ai_processor_id, "configured"

        parent = f"projects/{project}/locations/{s.document_ai_location}"
        try:
            processors = list(client.list_processors(parent=parent))
        except Exception:
            logger.warning(
                "document_ai processor discovery failed: could not list processors "
                "looking for display_name=%r under parent=%s",
                PABLO_OCR_PROCESSOR_NAME,
                parent,
            )
            return None, "discovered"

        for processor in processors:
            if getattr(processor, "display_name", None) == PABLO_OCR_PROCESSOR_NAME:
                processor_id = str(processor.name).rsplit("/", maxsplit=1)[-1]
                return processor_id, "discovered"

        logger.warning(
            "document_ai processor discovery found no match: looking for "
            "display_name=%r under parent=%s",
            PABLO_OCR_PROCESSOR_NAME,
            parent,
        )
        return None, "discovered"


# --- module-level helpers --------------------------------------------


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    import fitz  # type: ignore[import-untyped]

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return int(doc.page_count)


def _call_with_one_retry(fn: Any, request: Any) -> Any:
    """Call ``fn(request)`` through the shared retry engine.

    Transient = ServiceUnavailable / DeadlineExceeded / RetryError (the
    default classifier's read on these gax exception types) → retry
    once via ``HTTP_REQUEST``. Anything else → log + return ``None``.
    ``retry=None`` disables the SDK's own 300s retry loop so we own
    the policy.
    """
    try:
        import google.api_core  # noqa: F401  — import guard only
    except ImportError:
        try:
            return fn(request=request)
        except Exception:
            logger.exception("document_ai call failed")
            return None

    call_kwargs = {
        "request": request,
        "timeout": _PROCESS_TIMEOUT_SECONDS,
        "retry": None,
    }

    try:
        return call_with_retry(
            lambda: fn(**call_kwargs),
            policy=HTTP_REQUEST,
            idempotency=Idempotency.SAFE,
            on_retry=lambda _attempt, exc, _delay: logger.warning(
                "document_ai transient error: %s; retrying once", exc
            ),
            sleep=_retry_sleep,
        )
    except RetryExhaustedError:
        logger.exception("document_ai retry failed")
        return None
    except Exception:
        logger.exception("document_ai permanent error")
        return None


def _parse_response(response: Any, *, latency_ms: int) -> OcrResult:
    document = response.document
    text: str = document.text or ""
    pages = list(document.pages) if document.pages else []

    confidences: list[float] = []
    low_confidence_pages: list[int] = []
    for index, page in enumerate(pages, start=1):
        layout = getattr(page, "layout", None)
        confidence = float(getattr(layout, "confidence", 0.0) or 0.0)
        confidences.append(confidence)
        if confidence < _PAGE_CONFIDENCE_LOW_THRESHOLD:
            low_confidence_pages.append(index)

    page_count = len(pages)
    avg_confidence = sum(confidences) / page_count if page_count else 0.0

    flagged_overall = avg_confidence < _AVG_CONFIDENCE_LOW_THRESHOLD or (
        page_count > 0
        and len(low_confidence_pages) / page_count > _LOW_CONFIDENCE_PAGE_FRACTION_THRESHOLD
    )
    if flagged_overall and text:
        text = _LOW_CONFIDENCE_MARKER + text

    return OcrResult(
        text=text,
        page_count=page_count,
        avg_confidence=avg_confidence,
        low_confidence_pages=low_confidence_pages,
        latency_ms=latency_ms,
    )
