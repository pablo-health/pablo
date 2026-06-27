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
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..settings import Settings

logger = logging.getLogger(__name__)

_AVG_CONFIDENCE_LOW_THRESHOLD = 0.5

_LOW_CONFIDENCE_PAGE_FRACTION_THRESHOLD = 0.25
_PAGE_CONFIDENCE_LOW_THRESHOLD = 0.5

# Exposed as constants so tests can monkeypatch.
_RETRY_BACKOFF_SECONDS = 2.0

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

    # --- public API ---------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """True iff a processor is set and the kill-switch is on."""
        s = self._settings
        return bool(
            s.allow_document_ai_ocr and s.document_ai_project_id and s.document_ai_processor_id
        )

    def extract(self, *, pdf_bytes: bytes, mime_type: str) -> OcrResult | None:
        """OCR a PDF. Returns ``None`` on any soft failure."""
        if not self.is_configured:
            return None

        if mime_type != "application/pdf":
            return None

        try:
            client = self._get_client()
        except OcrUnavailableError:
            logger.warning("document_ai client unavailable; OCR skipped")
            return None

        page_count = _count_pdf_pages(pdf_bytes)
        if page_count > self._settings.document_ai_max_pages:
            logger.info(
                "document_ai OCR skipped: page_count=%d exceeds max=%d",
                page_count,
                self._settings.document_ai_max_pages,
            )
            return None

        request = self._build_request(pdf_bytes)
        start = time.monotonic()
        response = _call_with_one_retry(client.process_document, request)
        latency_ms = int((time.monotonic() - start) * 1000)

        if response is None:
            return None

        return _parse_response(response, latency_ms=latency_ms)

    # --- internals ----------------------------------------------------

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

    def _build_request(self, pdf_bytes: bytes) -> Any:
        from google.cloud import documentai  # type: ignore[attr-defined]

        s = self._settings
        processor_name = (
            f"projects/{s.document_ai_project_id}"
            f"/locations/{s.document_ai_location}"
            f"/processors/{s.document_ai_processor_id}"
        )
        raw_document = documentai.RawDocument(
            content=pdf_bytes,
            mime_type="application/pdf",
        )
        return documentai.ProcessRequest(
            name=processor_name,
            raw_document=raw_document,
        )


# --- module-level helpers --------------------------------------------


def _count_pdf_pages(pdf_bytes: bytes) -> int:
    import fitz  # type: ignore[import-untyped]

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return int(doc.page_count)


def _call_with_one_retry(fn: Any, request: Any) -> Any:
    """Call ``fn(request)`` with one retry on transient errors.

    Transient = ServiceUnavailable / DeadlineExceeded / RetryError →
    sleep + retry once. Anything else → log + return ``None``.
    ``retry=None`` disables the SDK's own 300s retry loop so we own
    the policy.
    """
    try:
        from google.api_core import exceptions as gax_exceptions
    except ImportError:
        try:
            return fn(request=request)
        except Exception:
            logger.exception("document_ai call failed")
            return None

    transient = (
        gax_exceptions.ServiceUnavailable,
        gax_exceptions.DeadlineExceeded,
        gax_exceptions.RetryError,
    )
    call_kwargs = {
        "request": request,
        "timeout": _PROCESS_TIMEOUT_SECONDS,
        "retry": None,
    }

    try:
        return fn(**call_kwargs)
    except transient as exc:
        logger.warning("document_ai transient error: %s; retrying once", exc)
        time.sleep(_RETRY_BACKOFF_SECONDS)
        try:
            return fn(**call_kwargs)
        except Exception:
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
