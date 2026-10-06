# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A scanned PDF is read with OCR wherever an upload is read for the AI.

The shared reader (``extract_document_text``) falls back to OCR when a PDF has
no text layer, keeps today's refusal when OCR is off, and never calls OCR for
a PDF that has text. ``read_document_text`` runs it off the event loop and
turns a stalled OCR call into a retryable error. The note import route is
checked end to end here; note-type derive in ``test_note_type_derive``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pymupdf
import pytest
from app.main import app
from app.models import Patient
from app.routes import sessions as sessions_routes
from app.services import note_import_service
from app.services.document_ai_ocr import (
    LOW_CONFIDENCE_MARKER,
    OcrResult,
    get_document_ocr_client,
)
from app.services.note_import_service import (
    DocumentReadTimeoutError,
    DocumentTextExtractionError,
    ParsedImportedNote,
    extract_document_text,
    read_document_text,
)
from app.services.note_service import NoteService
from app.services.session_service import SessionService

from tests.scanned_pdf_fakes import OCR_TEXT, SCANNED_PDF, TEXT_PDF, FakeOcrClient

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import (
        InMemoryNotesRepository,
        InMemoryPatientRepository,
        InMemoryTherapySessionRepository,
    )


def _read(data: bytes, ocr: FakeOcrClient | None) -> str:
    return extract_document_text(data, content_type="application/pdf", filename="note.pdf", ocr=ocr)


# ---------------------------------------------------------------------------
# The shared reader
# ---------------------------------------------------------------------------


def test_the_fixture_has_no_text_layer() -> None:
    with pymupdf.open(stream=SCANNED_PDF, filetype="pdf") as doc:
        assert "".join(page.get_text() for page in doc).strip() == ""
        assert doc.page_count == 3


def test_a_scanned_pdf_is_read_with_ocr() -> None:
    ocr = FakeOcrClient()

    assert _read(SCANNED_PDF, ocr) == OCR_TEXT
    assert ocr.calls == [len(SCANNED_PDF)]


def test_a_text_pdf_never_calls_ocr() -> None:
    ocr = FakeOcrClient()

    assert "SUBJECTIVE" in _read(TEXT_PDF, ocr).upper()
    assert ocr.calls == []


@pytest.mark.parametrize("ocr", [None, FakeOcrClient(is_configured=False)], ids=["none", "off"])
def test_with_ocr_off_a_scan_is_refused_as_before(ocr: FakeOcrClient | None) -> None:
    with pytest.raises(DocumentTextExtractionError, match="Scanned PDFs can't be read here"):
        _read(SCANNED_PDF, ocr)
    assert ocr is None or ocr.calls == []


@pytest.mark.parametrize(
    "result",
    [None, OcrResult(text="  ", page_count=3, avg_confidence=0.0)],
    ids=["ocr-failed", "ocr-found-nothing"],
)
def test_a_scan_ocr_cannot_read_is_refused(result: OcrResult | None) -> None:
    with pytest.raises(DocumentTextExtractionError, match="couldn't read this scanned PDF"):
        _read(SCANNED_PDF, FakeOcrClient(result=result))


def test_a_scan_over_the_page_cap_is_refused_without_an_ocr_call() -> None:
    ocr = FakeOcrClient(max_pages=2)

    with pytest.raises(DocumentTextExtractionError, match="more than 2 pages"):
        _read(SCANNED_PDF, ocr)
    assert ocr.calls == []


def test_the_low_confidence_marker_stays_out_of_the_text() -> None:
    ocr = FakeOcrClient(
        result=OcrResult(text=LOW_CONFIDENCE_MARKER + OCR_TEXT, page_count=3, avg_confidence=0.3)
    )

    assert _read(SCANNED_PDF, ocr) == OCR_TEXT


def test_ocr_logs_carry_counts_never_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    _read(SCANNED_PDF, FakeOcrClient())
    with pytest.raises(DocumentTextExtractionError):
        _read(SCANNED_PDF, FakeOcrClient(result=None))

    assert "pages=3" in caplog.text
    for words in ("sleeping", "evening walks", "PROGRESS NOTE"):
        assert words not in caplog.text


def test_reading_runs_off_the_event_loop() -> None:
    seen: list[int] = []

    class _ThreadRecordingOcr(FakeOcrClient):
        def extract(self, *, pdf_bytes: bytes, mime_type: str) -> OcrResult | None:
            seen.append(threading.get_ident())
            return super().extract(pdf_bytes=pdf_bytes, mime_type=mime_type)

    async def scenario() -> tuple[str, int]:
        text = await read_document_text(SCANNED_PDF, filename="scan.pdf", ocr=_ThreadRecordingOcr())
        return text, threading.get_ident()

    text, loop_thread = asyncio.run(scenario())

    assert text == OCR_TEXT
    assert seen
    assert seen[0] != loop_thread


def test_a_stalled_ocr_call_is_a_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(note_import_service, "DOCUMENT_READ_TIMEOUT_SECONDS", 0.2)
    hold = threading.Event()
    try:
        started = time.monotonic()
        with pytest.raises(DocumentReadTimeoutError, match="Try again"):
            asyncio.run(
                read_document_text(SCANNED_PDF, filename="scan.pdf", ocr=FakeOcrClient(hold=hold))
            )
        assert time.monotonic() - started < 2.0
    finally:
        hold.set()


# ---------------------------------------------------------------------------
# Note import
# ---------------------------------------------------------------------------

_PARSED = ParsedImportedNote(
    content={
        "subjective": {"client_narrative": "Client reported sleeping about six hours."},
        "objective": {},
        "assessment": {},
        "plan": {"next_steps": ["continue weekly sessions"]},
    },
    session_date=None,
    session_time=None,
)


class _RecordingParse:
    def __init__(self) -> None:
        self.source_texts: list[str] = []

    def parse_soap_note(self, source_text: str) -> ParsedImportedNote:
        self.source_texts.append(source_text)
        return _PARSED


@pytest.fixture
def parse() -> _RecordingParse:
    return _RecordingParse()


@pytest.fixture
def ocr() -> Iterator[FakeOcrClient]:
    fake = FakeOcrClient()
    app.dependency_overrides[get_document_ocr_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_document_ocr_client, None)
    if fake.hold is not None:
        fake.hold.set()


@pytest.fixture
def import_url(
    client: Any,
    parse: _RecordingParse,
    ocr: FakeOcrClient,
    mock_repo: InMemoryPatientRepository,
    mock_session_repo: InMemoryTherapySessionRepository,
    mock_notes_repo: InMemoryNotesRepository,
    mock_user_id: str,
) -> str:
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Test",
        last_name="Client",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        session_count=0,
        last_session_date=None,
    )
    mock_repo.create(patient, mock_user_id)
    app.dependency_overrides[sessions_routes.get_note_import_service] = lambda: parse
    app.dependency_overrides[sessions_routes.get_session_service] = lambda: SessionService(
        mock_session_repo, mock_repo, Mock(), NoteService(mock_notes_repo)
    )
    return f"/api/patients/{patient.id}/sessions/import"


def _scan() -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("scan.pdf", SCANNED_PDF, "application/pdf")}


def test_an_imported_scan_is_parsed_from_its_ocr_text(
    client: Any, import_url: str, parse: _RecordingParse, ocr: FakeOcrClient
) -> None:
    response = client.post(import_url, files=_scan())

    assert response.status_code == 201, response.text
    assert parse.source_texts == [OCR_TEXT]
    assert len(ocr.calls) == 1


def test_an_imported_scan_with_ocr_off_is_a_422(
    client: Any, import_url: str, parse: _RecordingParse, ocr: FakeOcrClient
) -> None:
    ocr.is_configured = False

    response = client.post(import_url, files=_scan())

    assert response.status_code == 422
    assert "Scanned PDFs can't be read here" in response.text
    assert parse.source_texts == []


def test_an_imported_scan_whose_ocr_stalls_is_a_retryable_503(
    client: Any,
    import_url: str,
    parse: _RecordingParse,
    ocr: FakeOcrClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(note_import_service, "DOCUMENT_READ_TIMEOUT_SECONDS", 0.2)
    ocr.hold = threading.Event()

    started = time.monotonic()
    response = client.post(import_url, files=_scan())

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DOCUMENT_READ_TIMEOUT"
    assert time.monotonic() - started < 2.0
    assert parse.source_texts == []
