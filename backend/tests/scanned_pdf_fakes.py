# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A scanned PDF and a stand-in OCR client for the upload readers' tests.

``SCANNED_PDF`` is the committed sample note with every page rendered to an
image (see ``fixtures/build_sample_soap_note.py``): no text layer, so the
reader has to fall back to OCR. Its content is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from app.services.document_ai_ocr import OcrResult

if TYPE_CHECKING:
    import threading

_FIXTURES = Path(__file__).parent / "fixtures"
SCANNED_PDF = (_FIXTURES / "sample_soap_note_scanned.pdf").read_bytes()
TEXT_PDF = (_FIXTURES / "sample_soap_note.pdf").read_bytes()

# What the stand-in "reads" off the scan. Synthetic, about no one.
OCR_TEXT = (
    "INDIVIDUAL THERAPY PROGRESS NOTE\n"
    "Date: 04/17/2026\n"
    "Subjective: Client reported sleeping about six hours a night, up from four.\n"
    "Plan: Continue weekly sessions and the evening walks."
)


@dataclass
class FakeOcrClient:
    """Stands in for ``DocumentAiOcrClient``; records each call.

    ``hold`` makes ``extract`` block until it is set, to stand for a stalled
    OCR call.
    """

    is_configured: bool = True
    max_pages: int = 30
    result: OcrResult | None = field(
        default_factory=lambda: OcrResult(text=OCR_TEXT, page_count=3, avg_confidence=0.97)
    )
    hold: threading.Event | None = None
    calls: list[int] = field(default_factory=list)

    def extract(self, *, pdf_bytes: bytes, mime_type: str) -> OcrResult | None:
        assert mime_type == "application/pdf"
        self.calls.append(len(pdf_bytes))
        if self.hold is not None:
            self.hold.wait(5.0)
        return self.result
