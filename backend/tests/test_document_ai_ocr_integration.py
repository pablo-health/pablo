# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Live Document AI integration test for the OCR fallback (ak6m.2.3).

Gated behind ``DOCAI_INTEGRATION=1`` so ``make test`` never pays
per-page fees. When enabled, six representative MTSamples Psychiatry
fixtures (see ``_SMOKE_FIXTURES``) are rasterized into image-only
PDFs, run against the real ``DocumentAiOcrClient``, and checked for
word-level coverage against the original text.

Run::

    DOCAI_INTEGRATION=1 \\
    DOCUMENT_AI_PROJECT_ID=<project> \\
    DOCUMENT_AI_PROCESSOR_ID=<processor-id> \\
    poetry run pytest backend/tests/test_document_ai_ocr_integration.py -v

Cost: ~$0.0015 / page; the smoke set runs in ≈1 min for a few cents.

Fixtures live under ``backend/tests/fixtures/mtsamples/psychiatry``,
sourced from MTSamples (https://mtsamples.com). See ``NOTICE.md``
in that directory for attribution. The smoke set is six of the 47
committed files, picked for content diversity (short structured note,
long narrative, acronym-dense discharge summary, numeric tables,
medication strings, free-form evaluation). The other 41 stay on disk
for future evals (chat-context relevance, document classification,
information extraction).

The test rasterizes via PyMuPDF (text-PDF → per-page PNG → image-only
PDF) so PyMuPDF returns below the scanned-PDF threshold, the way a
real faxed page does — without that, the OCR fallback wouldn't fire.
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

_MIN_WORD_COVERAGE = 0.95
_RASTER_DPI = 150  # ~clinical fax quality

# Diversity-picked smoke set. Each fixture exercises a different OCR
# failure mode (short structured / long narrative / acronyms / numeric
# tables / medication strings / free-form). The other 41 fixtures stay
# on disk for future evals but don't run here.
_SMOKE_FIXTURES = frozenset(
    {
        "mental-status-evaluation",
        "psych-consult-depression-1",
        "psychiatric-discharge-summary-1",
        "neuropsychological-evaluation-1",
        "recheck-of-adhd-meds",
        "psychological-evaluation",
    }
)


# --- helpers ---------------------------------------------------------


def _rasterize_to_image_only_pdf(text: str) -> bytes:
    """Render ``text`` to a text-PDF, then rasterize each page into
    a fresh PDF whose pages are PNGs — no embedded text layer."""
    text_buf = io.BytesIO()
    c = rl_canvas.Canvas(text_buf, pagesize=LETTER)
    _, height = LETTER
    margin = 54
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

    The length filter drops short stop words ("the", "and", "of")
    whose presence inflates coverage without measuring much.
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
    return sorted(p for p in _FIXTURES.glob("*.txt") if p.stem in _SMOKE_FIXTURES)


# --- the test --------------------------------------------------------


@pytest.mark.parametrize("fixture_path", _fixture_paths(), ids=lambda p: p.stem)
def test_ocr_round_trip_recovers_majority_of_words(
    fixture_path: Path,
) -> None:
    """For each MTSamples fixture, rasterize → OCR → assert coverage."""
    original_text = fixture_path.read_text(encoding="utf-8")
    scanned_pdf = _rasterize_to_image_only_pdf(original_text)

    # Confirm the rasterized PDF would actually trip the fallback path.
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

    # Logged on every fixture so a passing run still surfaces drift
    # in average OCR quality after a processor-version bump.
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
