# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for DocumentAiOcrClient (THERAPY-ak6m.2.3).

Exercises the wrapper around Google's Document AI ``processDocument``
without touching the real API. The underlying client is faked via the
``client_factory`` injection point. Coverage focuses on the policy
choices the wrapper enforces:

* Soft-failure semantics (unconfigured, page cap, transient error,
  permanent error → ``None``).
* Confidence post-processing (per-page flagging + the overall
  low-confidence marker prepended to the body).
* The one-retry-then-give-up policy for transient errors.
* Lazy, cached, config-overridable project/processor discovery.

Real-API behavior is covered by an opt-in integration test gated
behind ``DOCAI_INTEGRATION=1`` (not in this file).
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import pytest
from app.services import document_ai_ocr as ocr_module
from app.services.document_ai_ocr import (
    _LOW_CONFIDENCE_MARKER,
    PABLO_OCR_PROCESSOR_NAME,
    DocumentAiOcrClient,
    OcrResult,
)
from app.settings import Settings
from google.api_core import exceptions as gax_exceptions
from reportlab.pdfgen import canvas

# ---- fakes mimicking the documentai client surface --------------------


@dataclass
class _FakeLayout:
    confidence: float


@dataclass
class _FakePage:
    layout: _FakeLayout


@dataclass
class _FakeDocument:
    text: str
    pages: list[_FakePage]


@dataclass
class _FakeResponse:
    document: _FakeDocument


@dataclass
class _FakeProcessor:
    name: str
    display_name: str


@dataclass
class _FakeDocAiClient:
    """Records ``process_document``/``list_processors`` calls.

    The real client raises ``google.api_core.exceptions``; tests that
    need to exercise the retry path use ``raises`` instead.
    """

    response: _FakeResponse | None = None
    raises: list[BaseException] = field(default_factory=list)
    calls: list[Any] = field(default_factory=list)
    processors: list[_FakeProcessor] = field(default_factory=list)
    list_processors_calls: list[str] = field(default_factory=list)
    list_processors_raises: BaseException | None = None

    def process_document(self, request: Any, **kwargs: Any) -> _FakeResponse:
        # Real client also takes timeout= and retry=; accept them so the
        # wrapper's call signature stays decoupled from these tests.
        self.calls.append(request)
        if self.raises:
            raise self.raises.pop(0)
        if self.response is None:
            raise RuntimeError("test bug: no response scripted")
        return self.response

    def list_processors(self, parent: str) -> list[_FakeProcessor]:
        self.list_processors_calls.append(parent)
        if self.list_processors_raises is not None:
            raise self.list_processors_raises
        return self.processors


# ---- helpers ----------------------------------------------------------


def _settings(
    *,
    processor_id: str | None = "abc123",
    project_id: str | None = "pablohealth-test",
    max_pages: int = 30,
    enabled: bool = True,
) -> Settings:
    return Settings(
        database_url="postgresql://t:t@l/t",
        document_ai_project_id=project_id,
        document_ai_processor_id=processor_id,
        document_ai_location="us",
        document_ai_max_pages=max_pages,
        allow_document_ai_ocr=enabled,
    )


def _pdf_with_pages(n: int) -> bytes:
    """Build a real ``n``-page PDF so PyMuPDF's page count is honest."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i in range(n):
        c.drawString(72, 720, f"page {i + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def _fake_response(*, text: str, confidences: list[float]) -> _FakeResponse:
    pages = [_FakePage(layout=_FakeLayout(confidence=c)) for c in confidences]
    return _FakeResponse(document=_FakeDocument(text=text, pages=pages))


# ---- is_configured short-circuit -------------------------------------


class TestIsConfigured:
    def test_returns_none_when_kill_switch_off(self) -> None:
        client = DocumentAiOcrClient(
            settings=_settings(enabled=False),
            client_factory=_FakeDocAiClient,
        )
        assert client.is_configured is False
        assert client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf") is None

    def test_true_with_kill_switch_on_even_if_unset(self) -> None:
        # Project/processor may still fail to resolve, but that's a separate,
        # explicitly logged failure — not reflected in is_configured.
        client = DocumentAiOcrClient(
            settings=_settings(processor_id=None, project_id=None),
            client_factory=_FakeDocAiClient,
        )
        assert client.is_configured is True


# ---- project/processor discovery --------------------------------------


class TestDiscovery:
    def test_project_resolved_from_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-project")
        fake = _FakeDocAiClient(response=_fake_response(text="x", confidences=[0.9]))
        client = DocumentAiOcrClient(
            settings=_settings(project_id=None), client_factory=lambda: fake
        )

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is not None
        assert fake.calls[0].name == "projects/env-project/locations/us/processors/abc123"
        assert fake.list_processors_calls == []

    def test_project_falls_back_to_google_auth_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        monkeypatch.setattr("google.auth.default", lambda: (object(), "auth-project"))
        fake = _FakeDocAiClient(response=_fake_response(text="x", confidences=[0.9]))
        client = DocumentAiOcrClient(
            settings=_settings(project_id=None), client_factory=lambda: fake
        )

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is not None
        assert fake.calls[0].name == "projects/auth-project/locations/us/processors/abc123"

    def test_processor_discovered_by_display_name(self) -> None:
        fake = _FakeDocAiClient(
            response=_fake_response(text="x", confidences=[0.9]),
            processors=[
                _FakeProcessor(
                    name="projects/pablohealth-test/locations/us/processors/aaa111",
                    display_name="some-other-processor",
                ),
                _FakeProcessor(
                    name="projects/pablohealth-test/locations/us/processors/bbb222",
                    display_name=PABLO_OCR_PROCESSOR_NAME,
                ),
            ],
        )
        client = DocumentAiOcrClient(
            settings=_settings(processor_id=None), client_factory=lambda: fake
        )

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is not None
        assert fake.calls[0].name == "projects/pablohealth-test/locations/us/processors/bbb222"
        assert fake.list_processors_calls == ["projects/pablohealth-test/locations/us"]

    def test_explicit_config_wins_and_skips_discovery_entirely(self) -> None:
        fake = _FakeDocAiClient(response=_fake_response(text="x", confidences=[0.9]))
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is not None
        assert fake.list_processors_calls == []

    def test_discovery_happens_at_most_once_per_instance(self) -> None:
        fake = _FakeDocAiClient(
            response=_fake_response(text="x", confidences=[0.9]),
            processors=[
                _FakeProcessor(
                    name="projects/pablohealth-test/locations/us/processors/bbb222",
                    display_name=PABLO_OCR_PROCESSOR_NAME,
                ),
            ],
        )
        client = DocumentAiOcrClient(
            settings=_settings(processor_id=None), client_factory=lambda: fake
        )

        client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")
        client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert len(fake.list_processors_calls) == 1

    def test_construction_makes_no_network_call(self) -> None:
        def _factory() -> Any:
            raise AssertionError("client factory should not run at construction time")

        DocumentAiOcrClient(settings=_settings(processor_id=None), client_factory=_factory)

    def test_no_matching_processor_degrades_gracefully_and_does_not_retry(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = _FakeDocAiClient(
            response=_fake_response(text="x", confidences=[0.9]),
            processors=[
                _FakeProcessor(
                    name="projects/pablohealth-test/locations/us/processors/aaa111",
                    display_name="some-other-processor",
                ),
            ],
        )
        client = DocumentAiOcrClient(
            settings=_settings(processor_id=None), client_factory=lambda: fake
        )

        with caplog.at_level("WARNING", logger=ocr_module.__name__):
            first = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")
        second = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert first is None
        assert second is None
        assert fake.calls == []
        assert len(fake.list_processors_calls) == 1
        assert PABLO_OCR_PROCESSOR_NAME in caplog.text
        assert "projects/pablohealth-test/locations/us" in caplog.text

    def test_kill_switch_off_skips_discovery_and_auth(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise_if_called() -> Any:
            raise AssertionError("google.auth.default should not run with the kill-switch off")

        monkeypatch.setattr("google.auth.default", _raise_if_called)
        fake = _FakeDocAiClient(response=_fake_response(text="x", confidences=[0.9]))
        client = DocumentAiOcrClient(
            settings=_settings(project_id=None, processor_id=None, enabled=False),
            client_factory=lambda: fake,
        )

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is None
        assert fake.calls == []
        assert fake.list_processors_calls == []

    def test_resolution_logged_once_with_no_document_details(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fake = _FakeDocAiClient(
            response=_fake_response(text="secret patient text", confidences=[0.9])
        )
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        with caplog.at_level("INFO", logger=ocr_module.__name__):
            client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        resolution_records = [r for r in caplog.records if "document_ai resolved" in r.message]
        assert len(resolution_records) == 1
        message = resolution_records[0].message
        assert "pablohealth-test" in message
        assert "us" in message
        assert "abc123" in message
        assert "configured" in message
        assert "secret patient text" not in message


# ---- happy path ------------------------------------------------------


class TestExtract:
    def test_success_returns_text_and_avg_confidence(self) -> None:
        fake = _FakeDocAiClient(
            response=_fake_response(text="hello world", confidences=[0.9, 0.95]),
        )
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(2), mime_type="application/pdf")

        assert isinstance(result, OcrResult)
        assert result.text == "hello world"
        assert result.page_count == 2
        assert result.avg_confidence == pytest.approx(0.925)
        assert result.low_confidence_pages == []
        assert len(fake.calls) == 1

    def test_low_confidence_page_is_flagged(self) -> None:
        fake = _FakeDocAiClient(
            response=_fake_response(text="hi", confidences=[0.92, 0.30]),
        )
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(2), mime_type="application/pdf")

        assert result is not None
        assert result.low_confidence_pages == [2]

    def test_overall_low_confidence_prepends_marker(self) -> None:
        # Avg < 0.5 → marker on the body so the downstream LLM sees
        # the uncertainty.
        fake = _FakeDocAiClient(
            response=_fake_response(text="garbled text", confidences=[0.30, 0.20]),
        )
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(2), mime_type="application/pdf")

        assert result is not None
        assert result.text.startswith(_LOW_CONFIDENCE_MARKER)
        assert "garbled text" in result.text

    def test_page_count_cap_skips_call(self) -> None:
        # 5-page PDF + cap of 3 → return None without calling the API
        fake = _FakeDocAiClient(response=_fake_response(text="x", confidences=[0.9]))
        client = DocumentAiOcrClient(settings=_settings(max_pages=3), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(5), mime_type="application/pdf")

        assert result is None
        assert fake.calls == []

    def test_non_pdf_mime_returns_none(self) -> None:
        fake = _FakeDocAiClient(response=_fake_response(text="x", confidences=[0.9]))
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        assert client.extract(pdf_bytes=b"\x89PNG\r\n", mime_type="image/png") is None
        assert fake.calls == []


# ---- error handling --------------------------------------------------


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the retry backoff so retry tests run in milliseconds."""
    monkeypatch.setattr(ocr_module, "_retry_sleep", lambda _seconds: None)


class TestErrorHandling:
    def test_transient_error_then_success(self) -> None:
        fake = _FakeDocAiClient(
            response=_fake_response(text="ok", confidences=[0.9]),
            raises=[gax_exceptions.ServiceUnavailable("retry me")],
        )
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is not None
        assert result.text == "ok"
        assert len(fake.calls) == 2  # 1 failure + 1 retry

    def test_transient_error_twice_gives_up(self) -> None:
        fake = _FakeDocAiClient(
            raises=[
                gax_exceptions.DeadlineExceeded("slow 1"),
                gax_exceptions.DeadlineExceeded("slow 2"),
            ],
        )
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is None
        assert len(fake.calls) == 2

    def test_permanent_error_returns_none_without_retry(self) -> None:
        fake = _FakeDocAiClient(
            raises=[gax_exceptions.PermissionDenied("no IAM for you")],
        )
        client = DocumentAiOcrClient(settings=_settings(), client_factory=lambda: fake)

        result = client.extract(pdf_bytes=_pdf_with_pages(1), mime_type="application/pdf")

        assert result is None
        assert len(fake.calls) == 1
