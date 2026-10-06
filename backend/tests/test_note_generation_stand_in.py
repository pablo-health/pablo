# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The end-to-end stack's stand-in for note drafting.

``note_generation_base_url`` sends standalone-note and preview drafts to an
HTTP service (``scripts/fake_llm.py``) instead of a model. These run the real
generation service against that service's app, so a draft comes back exactly
as the backend would validate a model's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.routes.notes import get_note_generation_service
from app.services import dictation_transcription, http_structured_llm_gateway
from app.services.dictation_transcription import HttpDictationTranscriber
from app.services.http_structured_llm_gateway import HttpStructuredLLMGateway
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.note_redraft import DICTATED_HEADING
from app.settings import Settings
from fastapi.testclient import TestClient

from scripts.fake_llm import DICTATION_TEXT, REFUSES_DRAFT
from scripts.fake_llm import app as fake_llm_app

from .test_practice_note_types import COACH_SPEC

if TYPE_CHECKING:
    import httpx

BASE_URL = "http://fake-llm:8083/notes"
NOW = datetime(2026, 10, 5, tzinfo=UTC)
PATIENT = Patient(id="p", first_name="", last_name="", created_at=NOW, updated_at=NOW)
TRANSCRIPT = Transcript(format="txt", content="[00:01] Therapist: Hello\n[00:03] Client: Hi")


@pytest.fixture
def stand_in(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Route the gateway's POSTs to the stand-in's app; return the URLs hit."""
    client = TestClient(fake_llm_app)
    urls: list[str] = []

    def post(url: str, *, json: dict[str, Any], timeout: float) -> httpx.Response:
        urls.append(url)
        response: httpx.Response = client.post(url.removeprefix("http://fake-llm:8083"), json=json)
        return response

    monkeypatch.setattr(http_structured_llm_gateway.httpx, "post", post)
    return urls


def _service() -> RegistryNoteGenerationService:
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)
    return RegistryNoteGenerationService(
        registry=registry, llm_gateway=HttpStructuredLLMGateway(BASE_URL)
    )


def test_a_practice_type_gets_a_draft_in_its_own_shape(stand_in: list[str]) -> None:
    definition = to_definition("custom.coach", 1, PracticeNoteTypeSpec.model_validate(COACH_SPEC))

    generated = _service().generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"segment": "Network"},
        definition=definition,
    )

    assert generated.content == {
        "fix": {"one_thing": "Stand-in draft for fix.one_thing."},
        "log_row": {"channels": ["Stand-in draft for log_row.channels."]},
    }
    assert stand_in == [f"{BASE_URL}/v1/structured"]


def test_a_field_named_after_an_input_is_drafted_as_its_value(stand_in: list[str]) -> None:
    """So a spec can see which value reached the prompt."""
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Visit",
            "sections": [
                {
                    "key": "billing",
                    "label": "Billing",
                    "fields": [
                        {"key": "visit_code", "label": "Visit code"},
                        {"key": "summary", "label": "Summary"},
                    ],
                }
            ],
            "inputs": [
                {"key": "visit_code", "label": "Visit code"},
                {"key": "program", "label": "Program"},
            ],
        }
    )
    definition = to_definition("custom.visit", 1, spec)

    generated = _service().generate_note(
        definition.key,
        TRANSCRIPT,
        PATIENT,
        NOW,
        inputs={"visit_code": "99214"},
        definition=definition,
    )

    assert generated.content == {
        "billing": {"visit_code": "99214", "summary": "Stand-in draft for billing.summary."}
    }
    assert len(stand_in) == 1


def test_a_diagnoses_field_gets_one_coded_diagnosis(stand_in: list[str]) -> None:
    spec = PracticeNoteTypeSpec.model_validate(
        {
            "label": "Evaluation",
            "sections": [
                {
                    "key": "assessment",
                    "label": "Assessment",
                    "fields": [{"key": "diagnoses", "label": "Diagnoses", "kind": "diagnoses"}],
                }
            ],
        }
    )
    definition = to_definition("custom.eval", 1, spec)

    generated = _service().generate_note(
        definition.key, TRANSCRIPT, PATIENT, NOW, definition=definition
    )

    assert generated.content == {
        "assessment": {
            "diagnoses": [
                {
                    "label": "Stand-in diagnosis for assessment.diagnoses",
                    "code": "F00.0",
                    "status": None,
                }
            ]
        }
    }


def test_what_was_dictated_lands_in_the_field_it_names(stand_in: list[str]) -> None:
    """A redraft with a dictation visibly gains it."""
    transcript = Transcript(
        format="txt",
        content=f"{TRANSCRIPT.content}\n\n{DICTATED_HEADING}\n\n{DICTATION_TEXT}",
    )

    generated = _service().generate_note("soap", transcript, PATIENT, NOW)

    assert generated.soap_note is not None
    assert generated.soap_note.plan.next_session.text == "Two weeks from today, same time."
    assert generated.soap_note.subjective.chief_complaint.text.startswith("Stand-in draft")


def test_every_dictated_clip_is_heard_as_the_same_words(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(fake_llm_app)

    def post(url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> Any:
        return client.post(
            url.removeprefix("http://fake-llm:8083"), content=content, headers=headers
        )

    monkeypatch.setattr(dictation_transcription.httpx, "post", post)
    transcriber = HttpDictationTranscriber("http://fake-llm:8083/transcription")

    assert transcriber.transcribe(b"\x1a\x45\xdf\xa3", "audio/webm") == DICTATION_TEXT


def test_a_soap_draft_survives_its_second_call(stand_in: list[str]) -> None:
    """SOAP asks again for sentence-to-transcript links; the stand-in answers none."""
    generated = _service().generate_note("soap", TRANSCRIPT, PATIENT, NOW)

    assert generated.soap_note is not None
    assert generated.soap_note.subjective.chief_complaint.text.startswith("Stand-in draft")
    assert len(stand_in) == 2


def test_a_refused_transcript_fails_its_draft_without_a_retry(stand_in: list[str]) -> None:
    """The stand-in's refusal is a failure the worker records at once, not one it retries."""
    transcript = Transcript(format="txt", content=f"[00:01] Therapist: {REFUSES_DRAFT}")

    with pytest.raises(ValueError, match="Note generation failed"):
        _service().generate_note("soap", transcript, PATIENT, NOW)

    assert len(stand_in) == 1


def test_the_route_dependency_uses_the_stand_in_only_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured = Settings(
        database_url="postgresql://x:x@localhost:5432/x",
        environment="development",
        note_generation_base_url=BASE_URL,
    )
    monkeypatch.setattr("app.routes.notes.get_settings", lambda: configured)
    service = get_note_generation_service()
    assert isinstance(service, RegistryNoteGenerationService)
    assert isinstance(service._llm_gateway, HttpStructuredLLMGateway)

    unconfigured = Settings(
        database_url="postgresql://x:x@localhost:5432/x", environment="development"
    )
    monkeypatch.setattr("app.routes.notes.get_settings", lambda: unconfigured)
    service = get_note_generation_service()
    assert isinstance(service, RegistryNoteGenerationService)
    assert not isinstance(service._llm_gateway, HttpStructuredLLMGateway)
