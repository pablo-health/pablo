# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A draft the model answered without filling any field fails loud.

Two shapes reach the coercion with nothing it can read: a tool call that
wrote each section as a string of JSON instead of an object, and an answer
whose sections are present with every field empty. The first is decoded at
the gateway; the second is asked once more and then fails the draft, rather
than being saved as a finished note with every section blank.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, ClassVar

import pytest
from app.models import Patient, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.reliability import SINGLE_ATTEMPT
from app.services.bedrock_structured_llm_gateway import BedrockStructuredLLMGateway
from app.services.note_generation_service import (
    EmptyNoteGenerationError,
    RegistryNoteGenerationService,
    TransientNoteGenerationError,
)
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
    decode_json_encoded_fields,
)

_SONNET = "bedrock:us.anthropic.claude-sonnet-4-6"
_WHEN = datetime.fromisoformat("2024-06-01T00:00:00+00:00")
_TRANSCRIPT = Transcript(
    format="txt",
    content=(
        "[00:00] Therapist: How was your week?\n"
        "[00:05] Client: Better. I used the breathing exercise twice."
    ),
)

_SOAP: dict[str, dict[str, Any]] = {
    "subjective": {
        "chief_complaint": "Follow-up on anxiety.",
        "mood_affect": "Calmer.",
        "symptoms": ["worry"],
        "client_narrative": "Used breathing exercise twice.",
    },
    "objective": {
        "appearance": "",
        "behavior": "Engaged.",
        "speech": "",
        "thought_process": "",
        "affect_observed": "",
    },
    "assessment": {
        "clinical_impression": "Improving.",
        "progress": "",
        "risk_assessment": "No risk indicators present.",
        "functioning_level": "",
    },
    "plan": {
        "interventions_used": [],
        "homework_assignments": [],
        "next_steps": ["Continue practice."],
        "next_session": "",
    },
}

#: Every section present, every field empty: what the coercion makes of an
#: answer it cannot read, and what a blank answer looks like on its own.
_EMPTY_SOAP: dict[str, dict[str, Any]] = {
    section: {key: [] if isinstance(value, list) else "" for key, value in fields.items()}
    for section, fields in _SOAP.items()
}


def _stringified(note: dict[str, Any]) -> dict[str, Any]:
    """Each section written as a string holding its JSON."""
    return {section: json.dumps(fields) for section, fields in note.items()}


def _converse(tool_input: dict[str, Any], *, output_tokens: int = 900) -> dict[str, Any]:
    """A Bedrock Converse response carrying one forced tool call."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "t1",
                            "name": "emit_structured_output",
                            "input": tool_input,
                        }
                    }
                ],
            }
        },
        "stopReason": "tool_use",
        "usage": {"inputTokens": 4000, "outputTokens": output_tokens, "totalTokens": 4900},
    }


class _StubClient:
    def __init__(self, *outcomes: dict[str, Any]) -> None:
        self.outcomes = list(outcomes)
        self.requests: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.requests.append(kwargs)
        return self.outcomes.pop(0)


@pytest.fixture
def registry() -> NoteTypeRegistry:
    reg = NoteTypeRegistry()
    register_builtin_note_types(reg)
    return reg


@pytest.fixture
def patient() -> Patient:
    return Patient(
        id="p1",
        first_name="Jane",
        last_name="Doe",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        diagnosis="Adjustment disorder",
    )


class TestDecodeJsonEncodedFields:
    _SCHEMA: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "section": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
            },
            "plain": {"type": "string"},
        },
    }

    def test_an_object_sent_as_json_text_is_decoded(self) -> None:
        data, decoded = decode_json_encoded_fields(
            {"section": json.dumps({"text": "a", "items": ["b"]}), "plain": "c"}, self._SCHEMA
        )
        assert data == {"section": {"text": "a", "items": ["b"]}, "plain": "c"}
        assert decoded == 1

    def test_a_nested_array_sent_as_json_text_is_decoded(self) -> None:
        data, decoded = decode_json_encoded_fields(
            {"section": {"text": "a", "items": '["b", "c"]'}}, self._SCHEMA
        )
        assert data == {"section": {"text": "a", "items": ["b", "c"]}}
        assert decoded == 1

    def test_a_string_field_that_looks_like_json_is_left_alone(self) -> None:
        data, decoded = decode_json_encoded_fields({"plain": '{"x": 1}'}, self._SCHEMA)
        assert data == {"plain": '{"x": 1}'}
        assert decoded == 0

    def test_text_that_is_not_json_or_the_wrong_type_is_left_alone(self) -> None:
        data, decoded = decode_json_encoded_fields(
            {"section": "not json", "other": "{}"}, self._SCHEMA
        )
        assert data == {"section": "not json", "other": "{}"}
        assert decoded == 0
        data, decoded = decode_json_encoded_fields({"section": "[1, 2]"}, self._SCHEMA)
        assert data == {"section": "[1, 2]"}
        assert decoded == 0


class TestStringifiedSectionsOnBedrock:
    def test_a_soap_draft_with_every_section_as_json_text_is_populated(
        self, registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        client = _StubClient(
            _converse(_stringified(_SOAP)),
            _converse({"attributions": []}, output_tokens=12),
        )
        gateway = BedrockStructuredLLMGateway(client_factory=lambda _timeout: client)
        service = RegistryNoteGenerationService(
            registry=registry, llm_gateway=gateway, model=_SONNET
        )

        result = service.generate_note("soap", _TRANSCRIPT, patient, _WHEN)

        assert result.soap_note is not None
        assert result.soap_note.subjective.chief_complaint.text == "Follow-up on anxiety."
        assert result.soap_note.plan.next_steps is not None
        assert [s.text for s in result.soap_note.plan.next_steps] == ["Continue practice."]
        # Drafted once, then attributed: no retry was needed.
        assert len(client.requests) == 2

    def test_the_gateway_returns_objects_for_stringified_sections(self) -> None:
        client = _StubClient(_converse(_stringified(_SOAP)))
        gateway = BedrockStructuredLLMGateway(client_factory=lambda _timeout: client)
        schema = {
            "type": "object",
            "properties": {
                section: {
                    "type": "object",
                    "properties": {key: {"type": "string"} for key in fields},
                }
                for section, fields in _SOAP.items()
            },
        }
        completion = gateway.complete_structured(
            model=_SONNET,
            system_prompt="SYSTEM",
            user_prompt="USER",
            response_schema=schema,
            max_output_tokens=4096,
            retry_policy=SINGLE_ATTEMPT,
        )
        assert completion.data == _SOAP
        assert completion.output_tokens == 900


class TestEmptyDraft:
    def test_an_empty_draft_is_asked_again(
        self, registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        gateway = FakeStructuredLLMGateway(
            responses=[
                StructuredCompletion(data=_EMPTY_SOAP, output_tokens=40),
                StructuredCompletion(data=_SOAP),
                StructuredCompletion(data={"attributions": []}),
            ]
        )
        service = RegistryNoteGenerationService(registry=registry, llm_gateway=gateway)

        result = service.generate_note("soap", _TRANSCRIPT, patient, _WHEN)

        assert result.soap_note is not None
        assert result.soap_note.subjective.chief_complaint.text == "Follow-up on anxiety."
        assert len(gateway.calls) == 3
        assert gateway.calls[0]["user_prompt"] == gateway.calls[1]["user_prompt"]

    def test_two_empty_drafts_fail_the_note(
        self,
        registry: NoteTypeRegistry,
        patient: Patient,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        gateway = FakeStructuredLLMGateway(
            default_response=StructuredCompletion(data=_EMPTY_SOAP, output_tokens=40)
        )
        service = RegistryNoteGenerationService(registry=registry, llm_gateway=gateway)

        with caplog.at_level("WARNING"), pytest.raises(EmptyNoteGenerationError) as exc_info:
            service.generate_note("soap", _TRANSCRIPT, patient, _WHEN)

        # A deterministic failure: the session is marked failed, not queued again.
        assert isinstance(exc_info.value, ValueError)
        assert not isinstance(exc_info.value, TransientNoteGenerationError)
        # Two drafts, and no attribution call for a note that has nothing in it.
        assert len(gateway.calls) == 2
        shapes = [r.getMessage() for r in caplog.records if "filled no field" in r.getMessage()]
        assert len(shapes) == 2
        assert "subjective=dict" in shapes[0]
        assert "output_tokens=40" in shapes[0]
        # The log carries the shape, never the content.
        assert "anxiety" not in caplog.text

    def test_an_unreadable_answer_fails_the_note(
        self, registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        """Sections the coercion cannot read are as empty as blank ones."""
        gateway = FakeStructuredLLMGateway(
            default_response=StructuredCompletion(data={"note": "free text"})
        )
        service = RegistryNoteGenerationService(registry=registry, llm_gateway=gateway)

        with pytest.raises(EmptyNoteGenerationError):
            service.generate_note("soap", _TRANSCRIPT, patient, _WHEN)

    def test_no_client_present_may_draft_empty(
        self, registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        gateway = FakeStructuredLLMGateway(
            default_response=StructuredCompletion(data={"note": {"body": ""}})
        )
        service = RegistryNoteGenerationService(registry=registry, llm_gateway=gateway)

        result = service.generate_note(
            "narrative", _TRANSCRIPT, patient, _WHEN, client_present_end_seconds=0
        )

        assert result.content == {"note": {"body": ""}}
        assert len(gateway.calls) == 1
