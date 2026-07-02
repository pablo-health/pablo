# Patient-document OCR fallback

## Goal

When `PatientDocumentsService.finalize_upload` runs `_extract_pdf_text` and
PyMuPDF returns `None` — i.e. the PDF has no embedded text and is presumed
scanned/faxed — fall back to **Google Document AI** (Document OCR processor)
to extract text. Store the result in `patient_documents.extracted_text`, the
same column the chat bundler already reads.

This unblocks the PMHNP pilot for patients whose prior-provider records
arrived as faxed paper rather than digital EHR exports.

## What already exists (don't rebuild)

* `backend/app/services/patient_documents_service.py::_extract_pdf_text`
  (line 333) — PyMuPDF extraction at finalize. Returns `None` if the body
  is shorter than `_SCANNED_PDF_TEXT_THRESHOLD` (100 chars), which is the
  hook point for this bead.
* `patient_documents` SQLAlchemy model in `backend/app/db/models.py`
  with `extracted_text: text | null` column.
* Chat bundler (`chat_context_bundler.py::_load_patient_documents`) reads
  `extracted_text` and filters out `None`-text docs as `skipped_no_text`
  in the manifest. Once OCR populates the field, those docs surface
  automatically — no bundler changes required for this bead.

## Why Document AI specifically (not Gemini, not Tesseract)

| Option | Why rejected for v1 |
|--------|--------------------|
| **Gemini native PDF input** | Cheap + zero new vendor, but no per-token confidence signal. A faxed page that's 80% illegible would come back as confidently-cited hallucinated text. For clinical use this is a worse failure mode than "couldn't read it." |
| **Tesseract (local)** | Mediocre on faxed clinical PDFs (multi-column layouts, low contrast). Operating a tesseract binary in the Docker image is meaningful operational cost for a mediocre result. |
| **AWS Textract / Anthropic Vision** | New vendor + separate BAA paperwork. Document AI is on the same GCP BAA as Cloud SQL. |

**Document AI Document OCR processor** is the right v1 choice:
- BAA covered under standard Google Cloud terms (already signed).
- Per-token confidence scores returned in the response — usable for "fall back
  to a `[low confidence — extraction may be inaccurate]` marker" instead of
  silent hallucination.
- $1.50 / 1k pages is acceptable at pilot scale.
- Handles multi-column layouts, tables (preserves text order), rotated pages.
- Synchronous API call, fits inside the existing `finalize_upload` flow
  without queue infrastructure.

## What to actually do

### 1. GCP provisioning (do this first — blocking)

* Enable `documentai.googleapis.com` in the project.
* Create a Document OCR processor in the chosen region (`us` recommended for
  latency from Cloud Run us-central). Note the processor ID.
* Confirm the Cloud Run service account has `roles/documentai.apiUser` on the
  project.
* Confirm the GCP BAA acknowledgement explicitly covers Document AI
  (Google's BAA scope page lists eligible services — check before pilot day).

### 2. Settings (`backend/app/settings.py`)

```python
class Settings(BaseSettings):
    ...
    # Document AI (OCR fallback)
    document_ai_project_id: str | None = None    # e.g. "pablo-prod"
    document_ai_location: str = "us"             # "us" or "eu"
    document_ai_processor_id: str | None = None  # resource id of the OCR processor
    document_ai_max_pages: int = 200             # refuse OCR for docs over N pages
    allow_document_ai_ocr: bool = True           # tenant kill-switch
```

When `document_ai_processor_id` is unset, the OCR fallback is a no-op — the
service still returns `extracted_text=None` for scanned PDFs, same as today.
This lets local dev + unit tests work without GCP creds.

### 3. New columns on `patient_documents` (`backend/app/db/models.py`)

```python
extracted_via:  Mapped[str | None]          = mapped_column(String, nullable=True)
                # null = never extracted; "pymupdf" = native text;
                # "document_ai" = OCR fallback; "unavailable" = both failed
extraction_metadata: Mapped[dict | None]    = mapped_column(JSONB, nullable=True)
                # {"page_count": int, "avg_confidence": float,
                #  "low_confidence_pages": [int], "latency_ms": int}
```

* Same-commit Alembic migration in `backend/alembic/versions/`.
* **Regenerate `tenant_template.sql`** via
  `poetry run python backend/scripts/regen_tenant_template.py` and commit
  the regenerated file in the same commit (CLAUDE.md guardrail #4).
* Default both columns to `NULL`. Backfill is not required — existing rows
  have `extracted_text` set or null, and the new columns are diagnostic.

### 4. OCR service module (`backend/app/services/document_ai_ocr.py`)

New module — keep it isolated so the dep on `google-cloud-documentai`
doesn't fan into the rest of the service layer.

```python
@dataclass(frozen=True)
class OcrResult:
    text: str
    page_count: int
    avg_confidence: float
    low_confidence_pages: list[int]
    latency_ms: int

class DocumentAiOcrClient:
    def __init__(self, *, settings: Settings) -> None: ...

    def extract(self, *, pdf_bytes: bytes, mime_type: str) -> OcrResult | None:
        """Synchronous OCR. Returns None when:
        - processor is unconfigured (dev / disabled tenant),
        - page count exceeds settings.document_ai_max_pages,
        - the Document AI call raises a known transient error after one retry.
        """
```

* Treat Document AI errors as a soft failure: log + audit, store
  `extracted_via="unavailable"`, leave `extracted_text=None`. The doc
  still appears in `list_for_patient`; the chat bundler skips it the same
  way it skips a pre-OCR scanned PDF.
* Confidence threshold: when `avg_confidence < 0.5` OR more than 25% of
  pages are flagged low-confidence, prepend the body with
  `"[extraction had low confidence — verify before relying on details]\n\n"`
  so the model sees the uncertainty. Don't refuse the document outright.

### 5. Wire into `finalize_upload`

```python
extracted_text = _extract_pdf_text(raw)
extracted_via: str | None = "pymupdf" if extracted_text else None
extraction_metadata: dict | None = None

if extracted_text is None and self._ocr is not None:
    ocr_result = self._ocr.extract(pdf_bytes=raw, mime_type=document.mime_type)
    if ocr_result is not None:
        extracted_text = ocr_result.text
        extracted_via = "document_ai"
        extraction_metadata = {
            "page_count": ocr_result.page_count,
            "avg_confidence": ocr_result.avg_confidence,
            "low_confidence_pages": ocr_result.low_confidence_pages,
            "latency_ms": ocr_result.latency_ms,
        }
        self._audit.log_patient_document_action(
            action=AuditAction.PATIENT_DOCUMENT_OCR_INVOKED,
            user_id=user_id,
            document_id=document.id,
            changes={"processor": "document_ai", **extraction_metadata},
        )
    else:
        extracted_via = "unavailable"
```

Pass the new fields to `mark_finalized` (add to the abstract method signature
and both impls).

### 6. Audit

Add to `AuditAction` enum:
```python
PATIENT_DOCUMENT_OCR_INVOKED = "patient_document_ocr_invoked"
```

Audited at OCR invocation, regardless of success. Log carries the doc id,
processor name, page count, latency, and avg confidence — no extracted text,
no filename (filename can contain PHI like patient name).

### 7. Cost protection

* `settings.document_ai_max_pages` (default 200): refuse OCR upfront, log
  reason, store `extracted_via="unavailable"`. The 25 MB upload cap already
  blocks most runaway docs; this is a second backstop on page count.
* No retry on Document AI errors except `ServiceUnavailable` /
  `DeadlineExceeded` — one retry with 2s backoff, then give up. Don't burn
  budget retrying through transient infra problems.
* **Defer to follow-up**: per-tenant monthly OCR spend cap with hard block.
  Acceptable risk at pilot scale; not acceptable past 10 tenants.

### 8. Per-tenant control

`settings.allow_document_ai_ocr: bool = True` is the global kill-switch.
Per-tenant override goes through the existing `tenant_settings` pattern (or
its placeholder — check `backend/app/settings.py` for what's already wired).
Tenants that haven't accepted the Document AI BAA delta can flip this off
without losing access to PyMuPDF extraction.

### 9. Tests

* **`backend/tests/test_document_ai_ocr.py`**: unit tests against a fake
  `DocumentAiClient` (mock the `google.cloud.documentai.DocumentProcessorServiceClient`).
  Cover: success path, page-count cap, transient error → retry → success,
  permanent error → soft failure, low-confidence marker prepended.
* **`backend/tests/test_patient_documents_service.py`**: add cases for
  finalize when `_extract_pdf_text` returns `None`:
  - OCR fallback succeeds → `extracted_text` populated, `extracted_via="document_ai"`,
    audit event emitted.
  - OCR client is `None` (unconfigured) → `extracted_via=None`, no audit.
  - OCR raises → `extracted_via="unavailable"`, soft failure.
* **Synthetic scanned-PDF fixture**: extend the existing reportlab helper in
  `test_patient_documents_service.py` to rasterize text to a PNG, embed in a
  PDF. PyMuPDF will return `<100 chars` on this; the test then verifies the
  OCR fallback path activates.
* **Skipped integration test**: gated behind `DOCAI_INTEGRATION=1` env var,
  runs `DocumentAiOcrClient` against the real GCP processor in dev. Don't
  enable in CI — pay per invocation.

### 10. Run, then ship

* `make check` clean (lint + mypy + tests).
* Manual smoke against 2-3 real pilot PDFs (or representative samples) —
  this is the actual go/no-go signal. Eval cases come later.

## Out of scope (do NOT add)

* Handwriting OCR — Document AI's general OCR is poor on cursive. If a
  pilot blocker emerges, switch to Document AI Form Parser ($30/1k pages)
  or a handwriting-specific processor as a follow-up bead.
* Table-structure preservation as structured data — the OCR processor
  preserves text order well enough for raw-text consumption.
* Multi-language detection — default to English-only; non-English docs
  return whatever Document AI's auto-detect gives them.
* Image / photo OCR — `mime_type in ("image/png", "image/jpeg")` already
  skips text extraction today; leave that alone.
* Re-OCR of previously-scanned docs whose first attempt failed. New uploads
  only. A backfill job is a future bead if needed.
* Per-page OCR cost tracking on the `chat_messages` row. Document AI cost
  is attributed at upload time, not at chat time; conflating them confuses
  the per-conversation cost story.
* Async / batch OCR via a job queue. Synchronous fits in `finalize_upload`
  for v1; revisit if a single doc takes >30s consistently.

## Project conventions

* Python 3.13+; `str | None` not `Optional[str]`; Pydantic for I/O.
* Poetry, not uv. `make check` (lint+mypy+test) is the bar — `pytest`-only
  passes are not "CI green" (CLAUDE.md guardrail #8).
* New model columns require a same-commit Alembic migration AND a
  regenerated `tenant_template.sql` (CLAUDE.md guardrail #4).
* No PHI in stdout. `logger.info` calls about OCR carry doc id, page count,
  confidence — never filename, never extracted text (CLAUDE.md guardrail #5).
* New routes touching PHI need an audit guardrail entry — but this bead
  doesn't add routes; the audit happens inside `finalize_upload` which is
  already audited as `PATIENT_DOCUMENT_FINALIZED`. The new
  `PATIENT_DOCUMENT_OCR_INVOKED` action is an additional row for the same
  request.

## Estimated effort

2-3 days end-to-end, breaking down roughly as:

| Step | Time |
|------|------|
| GCP provisioning (processor, IAM, BAA check) | 0.5 day (mostly waiting) |
| Settings + columns + migration + tenant_template regen | 0.5 day |
| `DocumentAiOcrClient` + wire into `finalize_upload` | 0.5 day |
| Tests (unit + synthetic fixture) | 0.5 day |
| Real-PDF smoke test + tune confidence threshold | 0.5-1 day |

## Risks

* **BAA scope.** Verify Google's published BAA explicitly covers Document AI
  before pilot. If not, this bead is blocked until paperwork closes.
* **Faxed-clinical-PDF quality.** Real-world scans vary wildly. Confidence
  threshold of 0.5 is a guess — calibrate against actual pilot samples
  before launch.
* **Latency.** Document AI sync OCR on a 100-page PDF can take 30-60s. The
  upload endpoint will block during finalize. If clinicians upload during a
  session, this is a poor UX. Mitigation: surface "processing…" in the UI
  via the existing `finalized_at IS NULL` filter on `list_for_patient`.
* **Cost surprise on backfill.** If anyone later adds a "re-OCR previously
  scanned docs" backfill job, it's easy to spend thousands of dollars in an
  afternoon. Per-tenant spend cap should land before any backfill.
