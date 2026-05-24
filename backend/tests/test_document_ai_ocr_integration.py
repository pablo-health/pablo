# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Live Document AI integration test for the OCR fallback (ak6m.2.3).

Gated behind ``DOCAI_INTEGRATION=1`` so the regular ``make test`` run
never pays per-page fees. When enabled, the test rasterizes each
MTSamples Psychiatry fixture into an **image-only PDF** (no embedded
text), confirms that PyMuPDF returns below the scanned-PDF threshold
(so the fallback would actually fire in production), calls the real
``DocumentAiOcrClient`` against the configured processor, and asserts
that a meaningful fraction of the original words survive the round
trip.

Run::

    DOCAI_INTEGRATION=1 \\
    DOCUMENT_AI_PROJECT_ID=pablohealth-dev \\
    DOCUMENT_AI_PROCESSOR_ID=e6e0da6723c7466c \\
    poetry run pytest backend/tests/test_document_ai_ocr_integration.py -v

Cost: ~$0.0015 / page at the time of writing. Each fixture renders
to 1-3 pages; the full 47-fixture sweep costs roughly $0.10-$0.20.

The fixtures live under ``backend/tests/fixtures/mtsamples/psychiatry``
and are sourced from MTSamples (https://mtsamples.com). See the
``NOTICE.md`` in that directory for attribution.

Why image-only PDFs (not text-PDFs lowered to <100 chars):
* The production fallback path runs only after PyMuPDF returns less
  than ``_SCANNED_PDF_TEXT_THRESHOLD`` (100 chars). Image-only PDFs
  reliably trip that, the way a real faxed page does.
* Per-character noise is closer to "fax-quality" than a clean text-
  PDF, so the coverage threshold tests honest OCR performance rather
  than verifying that we round-trip ASCII through compositing.
"""

from __future__ import annotations

import io
import os
import re
import unicodedata
from pathlib import Path

import fitz  # type: ignore[import-untyped]
import pytest
from app.services.document_ai_ocr import DocumentAiOcrClient
from app.services.patient_documents_service import _SCANNED_PDF_TEXT_THRESHOLD
from app.settings import Settings
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas as rl_canvas

_FIXTURES = Path(__file__).parent / "fixtures" / "mtsamples" / "psychiatry"

# Word-level coverage required for an OCR pass to count as "successful
# enough." The MTSamples corpus has medication names, abbreviations,
# and odd capitalization that Document AI sometimes misses on a fax-
# resolution render — 0.70 keeps the test sensitive to real regressions
# without flaking on per-fixture quirks. The harness logs the actual
# rate per file so trends are observable.
_MIN_WORD_COVERAGE = 0.70

# Render DPI for the synthetic "scanned" PDF. 150 DPI matches a typical
# clinical fax; high enough that Document AI can read it but low enough
# that the test actually exercises OCR (versus an effortless render).
_RASTER_DPI = 150


# --- helpers ---------------------------------------------------------


def _rasterize_to_image_only_pdf(text: str) -> bytes:
    """Turn ``text`` into a PDF whose pages are PNG images of the text.

    PyMuPDF rasterizes a reportlab text-PDF into per-page PNGs, which
    we then stack into a fresh PDF. The result has no embedded text
    layer — PyMuPDF's ``get_text`` returns roughly nothing, the same
    way it would for a faxed page.
    """
    # Step 1: clean text-PDF via reportlab (Helvetica is built in).
    text_buf = io.BytesIO()
    c = rl_canvas.Canvas(text_buf, pagesize=LETTER)
    _, height = LETTER
    margin = 54  # 0.75"
    line_height = 12
    max_chars_per_line = 95
    y = height - margin
    c.setFont("Helvetica", 9)
    for line in _wrap_lines(text, max_chars_per_line):
        if y < margin:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = height - margin
        c.drawString(margin, y, line)
        y -= line_height
    c.showPage()
    c.save()
    text_pdf_bytes = text_buf.getvalue()

    # Step 2: rasterize each page of the text PDF into a PNG and stack
    # them into an image-only PDF.
    scanned_buf = io.BytesIO()
    out_doc = fitz.open()
    with fitz.open(stream=text_pdf_bytes, filetype="pdf") as src:
        for page in src:
            pix = page.get_pixmap(dpi=_RASTER_DPI, alpha=False)
            png_bytes = pix.tobytes("png")
            img_rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
            new_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(img_rect, stream=png_bytes)
    out_doc.save(scanned_buf)
    out_doc.close()
    return scanned_buf.getvalue()


def _wrap_lines(text: str, max_chars_per_line: int) -> list[str]:
    """Crude word-wrap; reportlab's drawString doesn't wrap on its own."""
    lines: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split()
        current = ""
        for word in words:
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= max_chars_per_line:
                current = f"{current} {word}"
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")


def _tokenize(text: str) -> set[str]:
    """Lower-case alpha-numeric tokens, length >= 4.

    The length filter drops articles + short OCR confusables ("the",
    "and", "of") whose presence inflates coverage without measuring
    much. Unicode normalization handles smart quotes and other
    typographic substitutions OCR commonly emits.
    """
    normalized = unicodedata.normalize("NFKD", text).lower()
    return set(_TOKEN_RE.findall(normalized))


# --- skip plumbing ---------------------------------------------------


_INTEGRATION_ENABLED = os.environ.get("DOCAI_INTEGRATION") == "1"
_HAS_CREDS = bool(
    os.environ.get("DOCUMENT_AI_PROJECT_ID")
    and os.environ.get("DOCUMENT_AI_PROCESSOR_ID")
)

pytestmark = pytest.mark.skipif(
    not (_INTEGRATION_ENABLED and _HAS_CREDS),
    reason=(
        "Document AI integration test: set DOCAI_INTEGRATION=1 and "
        "DOCUMENT_AI_PROJECT_ID + DOCUMENT_AI_PROCESSOR_ID to enable. "
        "Costs ~$0.0015/page against the real GCP processor."
    ),
)


def _fixture_paths() -> list[Path]:
    return sorted(p for p in _FIXTURES.glob("*.txt") if p.name != "NOTICE.md")


# --- the test --------------------------------------------------------


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_ocr_round_trip_recovers_majority_of_words(
    fixture_path: Path,
) -> None:
    """For each MTSamples fixture, rasterize → OCR → assert coverage."""
    original_text = fixture_path.read_text(encoding="utf-8")
    scanned_pdf = _rasterize_to_image_only_pdf(original_text)

    # Sanity-check the fixture: PyMuPDF must see this as a scanned PDF
    # so the production fallback path would actually engage.
    with fitz.open(stream=scanned_pdf, filetype="pdf") as doc:
        embedded = "".join(page.get_text() for page in doc).strip()
    assert len(embedded) < _SCANNED_PDF_TEXT_THRESHOLD, (
        f"Rasterized {fixture_path.name} still has {len(embedded)} chars of "
        "embedded text — bug in the rasterizer, not OCR."
    )

    settings = Settings(
        database_url="postgresql://t:t@l/t",
        document_ai_project_id=os.environ["DOCUMENT_AI_PROJECT_ID"],
        document_ai_processor_id=os.environ["DOCUMENT_AI_PROCESSOR_ID"],
        document_ai_location=os.environ.get("DOCUMENT_AI_LOCATION", "us"),
        document_ai_max_pages=int(os.environ.get("DOCUMENT_AI_MAX_PAGES", "30")),
    )
    client = DocumentAiOcrClient(settings=settings)

    result = client.extract(pdf_bytes=scanned_pdf, mime_type="application/pdf")

    assert result is not None, f"OCR returned None for {fixture_path.name}"
    assert result.text, f"OCR returned empty text for {fixture_path.name}"

    original_tokens = _tokenize(original_text)
    ocr_tokens = _tokenize(result.text)
    coverage = len(original_tokens & ocr_tokens) / max(len(original_tokens), 1)

    # Log so a CI run produces a coverage histogram across the corpus
    # even when every fixture passes — useful for spotting drift after
    # a processor-version bump.
    print(
        f"{fixture_path.stem}: pages={result.page_count} "
        f"avg_conf={result.avg_confidence:.3f} "
        f"coverage={coverage:.3f}"
    )

    assert coverage >= _MIN_WORD_COVERAGE, (
        f"{fixture_path.name}: word-level coverage {coverage:.2%} below "
        f"{_MIN_WORD_COVERAGE:.0%} threshold "
        f"(avg_confidence={result.avg_confidence:.2f}, "
        f"low_conf_pages={result.low_confidence_pages})"
    )
