# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the imported-SOAP-note parse service.

Two layers:

- Deterministic unit tests inject a :class:`FakeStructuredLLMGateway` so
  they run in CI without network or credentials. These pin the contract:
  the SOAP registry schema (plus session date/time) is sent, the source
  text reaches the prompt, the response is coerced into note content, and
  the date/time are parsed.
- Optional local tests run real extraction (and, behind an env flag, a real
  LLM parse) against a sample SOAP PDF if one is present on disk. They are
  skipped in CI and never ship the sample file. To run the end-to-end parse
  locally against a real file::

      RUN_IMPORT_LLM_TEST=1 poetry run pytest \\
          backend/tests/test_note_import_service.py -o addopts="" -q

  with the sample at ~/Downloads/KN_SOAP_NOTE_020426.pdf (Vertex creds
  required for the LLM step).
"""

from __future__ import annotations

import os
from datetime import date, datetime, time
from pathlib import Path

import pytest

from backend.app.notes import get_default_registry, register_builtin_note_types
from backend.app.services.note_import_service import (
    DocumentTextExtractionError,
    NoteImportService,
    ParsedImportedNote,
    UnsupportedDocumentTypeError,
    check_grounding,
    extract_document_text,
)
from backend.app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
)


@pytest.fixture(autouse=True)
def _register_builtin_note_types() -> None:
    """Ensure SOAP/Narrative are in the default registry.

    In the app these are registered at startup; a bare unit test process
    hasn't run startup, so register them here. ``replace=True`` keeps it
    idempotent across tests.
    """
    register_builtin_note_types(get_default_registry())


# A complete, registry-shaped SOAP response as the model would return it,
# with the two extra session date/time keys the import schema adds. The
# content is deliberately bland, synthetic placeholder text — these tests
# only exercise field mapping and date parsing, not clinical realism.
_FAKE_SOAP_RESPONSE = {
    "subjective": {
        "chief_complaint": "Follow-up for stress management.",
        "mood_affect": "Reports feeling calmer this week.",
        "symptoms": ["occasional stress", "mild fatigue"],
        "client_narrative": "Client discussed progress on weekly goals.",
    },
    "objective": {
        "appearance": "Well groomed.",
        "behavior": "Cooperative and engaged.",
        "speech": "Normal rate and volume.",
        "thought_process": "Linear and goal-directed.",
        "affect_observed": "Bright and congruent.",
    },
    "assessment": {
        "clinical_impression": "Adjusting well; steady progress.",
        "progress": "Improving since last session.",
        "risk_assessment": "No safety concerns reported.",
        "functioning_level": "Functioning well day to day.",
    },
    "plan": {
        "interventions_used": ["supportive listening", "goal review"],
        "homework_assignments": ["practice a breathing exercise"],
        "next_steps": ["continue weekly sessions"],
        "next_session": "Next week, same time",
    },
    "session_date": "2026-02-04",
    "session_time": "",
}


def _fake_service(response: dict) -> NoteImportService:
    gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data=response))
    return NoteImportService(llm_gateway=gateway)


class TestParseSoapNote:
    def test_maps_sections_and_extracts_date(self) -> None:
        result = _fake_service(_FAKE_SOAP_RESPONSE).parse_soap_note("S/O/A/P source text")

        assert isinstance(result, ParsedImportedNote)
        assert result.session_date == date(2026, 2, 4)
        # Document stated no session time -> None (the "time if it exists" case).
        assert result.session_time is None

        # Content is the registry SOAP shape, ready for the editor unchanged.
        assert set(result.content) == {"subjective", "objective", "assessment", "plan"}
        assert result.content["subjective"]["chief_complaint"] == "Follow-up for stress management."
        assert result.content["subjective"]["symptoms"] == [
            "occasional stress",
            "mild fatigue",
        ]
        assert result.content["plan"]["homework_assignments"] == ["practice a breathing exercise"]

    def test_parses_time_when_present(self) -> None:
        response = {**_FAKE_SOAP_RESPONSE, "session_time": "14:30"}
        result = _fake_service(response).parse_soap_note("text")

        assert result.session_time == time(14, 30)
        assert result.session_datetime() == datetime(2026, 2, 4, 14, 30)

    def test_missing_date_yields_none(self) -> None:
        response = {**_FAKE_SOAP_RESPONSE, "session_date": "", "session_time": ""}
        result = _fake_service(response).parse_soap_note("text")

        assert result.session_date is None
        assert result.session_datetime() is None

    def test_unparseable_date_is_dropped_not_raised(self) -> None:
        response = {**_FAKE_SOAP_RESPONSE, "session_date": "February 4th"}
        result = _fake_service(response).parse_soap_note("text")

        assert result.session_date is None

    def test_session_datetime_defaults_time_to_midnight(self) -> None:
        result = _fake_service(_FAKE_SOAP_RESPONSE).parse_soap_note("text")

        assert result.session_datetime() == datetime(2026, 2, 4, 0, 0)

    def test_sends_soap_schema_with_session_fields_and_source_text(self) -> None:
        gateway = FakeStructuredLLMGateway(
            default_response=StructuredCompletion(data=_FAKE_SOAP_RESPONSE)
        )
        NoteImportService(llm_gateway=gateway).parse_soap_note("UNIQUE-SOURCE-MARKER")

        assert len(gateway.calls) == 1
        call = gateway.calls[0]
        props = call["response_schema"]["properties"]
        # SOAP sections plus the two session fields we inject.
        assert {"subjective", "objective", "assessment", "plan"} <= set(props)
        assert "session_date" in props
        assert "session_time" in props
        # The source note text is handed to the model verbatim.
        assert "UNIQUE-SOURCE-MARKER" in call["user_prompt"]


class TestExtractDocumentText:
    def test_extracts_plain_text(self) -> None:
        text = extract_document_text(
            b"  Subjective: client reported...  ",
            content_type="text/plain",
            filename="note.txt",
        )
        assert text == "Subjective: client reported..."

    def test_empty_text_file_raises(self) -> None:
        with pytest.raises(DocumentTextExtractionError):
            extract_document_text(b"   ", content_type="text/plain", filename="n.txt")

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(UnsupportedDocumentTypeError):
            extract_document_text(
                b"PK\x03\x04", content_type="application/zip", filename="archive.zip"
            )


class TestGrounding:
    _SOURCE = (
        "Client reported improved sleep and lower stress this week. "
        "Affect was bright. Plan: continue weekly sessions and a breathing exercise."
    )

    def test_flags_fabrication_passes_verbatim(self) -> None:
        content = {
            "subjective": {
                # Verbatim substring of the source.
                "client_narrative": "Client reported improved sleep and lower stress this week.",
                # Not in the source at all — fabricated.
                "chief_complaint": "Patient is training for a marathon.",
            },
            "objective": {"affect_observed": "Affect was bright."},
            "plan": {"next_steps": ["continue weekly sessions"]},
        }
        by_path = {g.path: g for g in check_grounding(content, self._SOURCE)}

        assert by_path["subjective.client_narrative"].grounded
        assert by_path["objective.affect_observed"].grounded
        assert by_path["plan.next_steps[0]"].grounded
        assert not by_path["subjective.chief_complaint"].grounded

    def test_high_overlap_counts_as_grounded(self) -> None:
        # Words all drawn from the source but not one contiguous substring.
        content = {"assessment": {"clinical_impression": "improved sleep and lower stress"}}
        result = check_grounding(content, self._SOURCE)[0]

        assert result.overlap == 1.0
        assert result.grounded

    def test_empty_fields_are_skipped(self) -> None:
        content = {"subjective": {"chief_complaint": "", "symptoms": []}}
        assert check_grounding(content, "anything") == ()

    def test_parse_attaches_grounding(self) -> None:
        # The fixture content does not appear in this source, so every field
        # is flagged — confirming the guard is wired into the parse result.
        result = _fake_service(_FAKE_SOAP_RESPONSE).parse_soap_note("unrelated source text")

        assert len(result.grounding) > 0
        assert result.ungrounded == result.grounding


# ---------------------------------------------------------------------------
# Optional local tests against a real sample PDF (never checked in).
# ---------------------------------------------------------------------------

_SAMPLE_PDF = Path.home() / "Downloads" / "KN_SOAP_NOTE_020426.pdf"


@pytest.mark.skipif(not _SAMPLE_PDF.exists(), reason="local sample PDF not present")
def test_extract_real_sample_pdf() -> None:
    """Real PyMuPDF extraction on a real PDF — no credentials needed."""
    text = extract_document_text(
        _SAMPLE_PDF.read_bytes(),
        content_type="application/pdf",
        filename=_SAMPLE_PDF.name,
    )
    upper = text.upper()
    assert "SUBJECTIVE" in upper
    assert "ASSESSMENT" in upper
    assert len(text) > 500


@pytest.mark.skipif(
    not _SAMPLE_PDF.exists() or os.getenv("RUN_IMPORT_LLM_TEST") != "1",
    reason="set RUN_IMPORT_LLM_TEST=1 with the sample PDF present (Vertex creds required)",
)
def test_parse_real_sample_pdf_end_to_end() -> None:
    """Full extraction + real LLM parse against the sample PDF."""
    text = extract_document_text(
        _SAMPLE_PDF.read_bytes(),
        content_type="application/pdf",
        filename=_SAMPLE_PDF.name,
    )
    result = NoteImportService().parse_soap_note(text)

    # The sample is dated 02/04/2026 with no explicit session time.
    assert result.session_date == date(2026, 2, 4)
    # Faithful extraction should populate the narrative sections.
    assert result.content["subjective"]["client_narrative"].strip()
    assert result.content["assessment"]["clinical_impression"].strip()
