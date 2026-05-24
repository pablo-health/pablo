# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Document AI OCR fallback for scanned patient PDFs (THERAPY-ak6m.2.3).

When ``PatientDocumentsService.finalize_upload`` runs PyMuPDF and the
result is below the "looks like a scanned doc" threshold, this module
handles the fallback to Google's Document AI OCR processor. The
service treats every OCR call as best-effort: an exception, a
configuration gap, or a doc that's too large to OCR sync all map to
``None`` so finalize completes and the doc lands in the bundler as
``skipped_no_text`` rather than 500ing the upload.

Design choices encoded here (see ``docs/architecture/patient-
documents-ocr-oss.md`` for the why):

* **Sync only.** Document AI's online ``processDocument`` API is the
  v1 surface — fits inside the existing finalize flow without queue
  infrastructure. The API rejects requests over ~30 pages for the
  OCR processor, so anything bigger is refused here too with a clear
  ``unavailable`` outcome. Async batch processing is a follow-up
  bead (and the existing ``transcription_task_queue`` pattern is the
  template for it).
* **One retry, only on transient errors.** Don't burn budget
  retrying through stable infra problems.
* **Confidence is surfaced, not gated.** Low-confidence pages get
  flagged in metadata + the body is prefixed with a "verify before
  relying on details" marker so the downstream LLM sees the
  uncertainty. We deliberately do not refuse a low-confidence doc
  outright — a partial extraction is more useful than nothing.
* **Hard dep on ``google-cloud-documentai`` is import-deferred.** The
  factory raises a clean ``OcrUnavailableError`` when the package
  isn't installed (relevant for slim self-host images that don't
  need the OCR path).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..settings import Settings

logger = logging.getLogger(__name__)

# Threshold above which we consider an OCR result low-confidence
# overall and prefix the body with a warning marker. Calibrated from
# the design doc against expected faxed-PDF quality; revisit once we
# have real Kendra samples scored.
_AVG_CONFIDENCE_LOW_THRESHOLD = 0.5

# Fraction of pages that may be flagged low-confidence before we
# treat the whole doc as low-confidence (even if avg is OK — a few
# bad pages in an otherwise clean doc still warrant the marker).
_LOW_CONFIDENCE_PAGE_FRACTION_THRESHOLD = 0.25

# Per-page confidence below this is flagged as a "low confidence
# page". Matches the avg threshold for a simple, defensible mental
# model; tune separately if real samples push for it.
_PAGE_CONFIDENCE_LOW_THRESHOLD = 0.5

# Backoff between the initial call and the single retry on transient
# errors. Exposed as a module-level constant so tests can monkeypatch
# it down to 0; production keeps the 2s default.
_RETRY_BACKOFF_SECONDS = 2.0

_LOW_CONFIDENCE_MARKER = (
    "[extraction had low confidence — verify before relying on details]\n\n"
)


class OcrUnavailableError(RuntimeError):
    """Raised when the OCR client can't be constructed.

    Distinguishes "configured but failed" (logged as a soft failure
    inside ``extract`` → returns ``None``) from "not even set up"
    (factory raises so the caller can decide whether to fall back to
    a no-op client or surface a config error).
    """


@dataclass(frozen=True)
class OcrResult:
    """Result of a successful Document AI extraction.

    ``text`` is already prefixed with the low-confidence marker when
    applicable; the caller stores it verbatim. Metadata is surfaced
    separately for audit + diagnostics.
    """

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
        """True iff settings carry a processor id AND the kill-switch
        is on. Service layer checks this to decide whether to call
        ``extract`` at all — keeps the "no-op when unconfigured"
        behavior visible at the call site rather than buried in a
        silent ``None`` return.
        """
        s = self._settings
        return bool(
            s.allow_document_ai_ocr
            and s.document_ai_project_id
            and s.document_ai_processor_id
        )

    def extract(self, *, pdf_bytes: bytes, mime_type: str) -> OcrResult | None:
        """Run OCR on a PDF blob. Returns ``None`` on soft failure.

        Returns ``None`` for:

        * Unconfigured client (kill-switch off, missing processor id,
          or the optional ``google-cloud-documentai`` dep not
          installed).
        * Doc exceeds ``settings.document_ai_max_pages`` (caught
          before the API call — Document AI's sync OCR caps around
          30 pages anyway).
        * Transient API error that persists past one retry.
        * Permanent API error (Unauthenticated, InvalidArgument,
          etc.) — logged + treated as soft failure so finalize
          completes.

        Raises only for programmer errors (bad arg types).
        """
        if not self.is_configured:
            return None

        if mime_type != "application/pdf":
            # Image OCR is out of scope for v1 (design doc); the
            # service skips this code path for PNG/JPEG already.
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
            # Already logged inside _call_with_one_retry.
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
            raise OcrUnavailableError(
                "google-cloud-documentai not installed"
            ) from exc

        endpoint = f"{self._settings.document_ai_location}-documentai.googleapis.com"
        self._client = documentai.DocumentProcessorServiceClient(
            client_options=ClientOptions(api_endpoint=endpoint)
        )
        return self._client

    def _build_request(self, pdf_bytes: bytes) -> Any:
        # Built lazily so the import only fires when actually invoking
        # OCR (keeps unit tests + slim images that don't have the dep
        # importable).
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
    """Cheap page count via PyMuPDF (already a dependency).

    Used as a pre-flight check before the Document AI call so we
    don't pay for a request that the sync API will reject anyway.
    """
    import fitz  # type: ignore[import-untyped]

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return int(doc.page_count)


def _call_with_one_retry(fn: Any, request: Any) -> Any:
    """Call ``fn(request)`` with a single retry on transient errors.

    Distinguishes:

    * Transient (``ServiceUnavailable``, ``DeadlineExceeded``) → one
      retry with a 2s backoff.
    * Permanent (``Unauthenticated``, ``PermissionDenied``,
      ``InvalidArgument``, anything else) → log + return ``None``.

    Returns the response or ``None`` on soft failure.
    """
    try:
        from google.api_core import exceptions as gax_exceptions
    except ImportError:
        # Without google.api_core we can't distinguish error classes,
        # so any exception becomes a soft failure.
        try:
            return fn(request=request)
        except Exception:
            logger.exception("document_ai call failed (no api_core for retry classes)")
            return None

    transient = (gax_exceptions.ServiceUnavailable, gax_exceptions.DeadlineExceeded)
    try:
        return fn(request=request)
    except transient as exc:
        logger.warning("document_ai transient error: %s; retrying once", exc)
        time.sleep(_RETRY_BACKOFF_SECONDS)
        try:
            return fn(request=request)
        except Exception:
            logger.exception("document_ai retry failed; treating as soft failure")
            return None
    except Exception:
        logger.exception("document_ai permanent error; treating as soft failure")
        return None


def _parse_response(response: Any, *, latency_ms: int) -> OcrResult:
    """Extract text + per-page confidence from a Document AI response.

    The Document AI response shape we care about:

    * ``response.document.text`` — the full OCR'd text, ordered.
    * ``response.document.pages[i].layout.confidence`` — per-page
      score in ``[0, 1]``. Some processor versions emit 0.0 for
      pages where the layout itself is uncertain; we treat 0.0 the
      same as any other low score.
    """
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
        and len(low_confidence_pages) / page_count
        > _LOW_CONFIDENCE_PAGE_FRACTION_THRESHOLD
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
