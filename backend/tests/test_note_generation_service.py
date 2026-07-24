# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for note generation service (SOAP + registry-driven types)."""

import os

os.environ["ENVIRONMENT"] = "development"

from datetime import datetime
from typing import Any

import pytest
from app.models import Patient, SOAPNote, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.notes.builtin import NARRATIVE_DEFINITION
from app.notes.registry import (
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeDefinition,
)
from app.services.note_generation_service import (
    GeneratedNote,
    MockNoteGenerationService,
    RegistryNoteGenerationService,
    TransientNoteGenerationError,
    _coerce_content_to_soap_note,
    _is_transient_llm_error,
)
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
    StructuredOutputTruncatedError,
)
from app.settings import get_settings


@pytest.fixture
def full_soap_json() -> dict[str, Any]:
    """Full structured SOAP JSON with all sub-fields populated."""
    return {
        "subjective": {
            "chief_complaint": "Increased anxiety related to work stress.",
            "mood_affect": "Anxious, restless, but engaged.",
            "symptoms": [
                "Difficulty sleeping",
                "Racing thoughts",
                "Muscle tension",
            ],
            "client_narrative": "Client describes feeling overwhelmed by deadlines and "
            "reports using breathing exercises with partial success.",
        },
        "objective": {
            "appearance": "Well-groomed, casually dressed.",
            "behavior": "Cooperative, fidgeted with hands during discussion of stressors.",
            "speech": "Normal rate and volume, occasionally pressured.",
            "thought_process": "Linear and goal-directed.",
            "affect_observed": "Anxious, congruent with reported mood.",
        },
        "assessment": {
            "clinical_impression": "Generalized Anxiety Disorder, moderate severity. "
            "Client shows insight into triggers.",
            "progress": "Moderate improvement in coping skill utilization since last session.",
            "risk_assessment": "No suicidal or homicidal ideation. No self-harm behaviors. "
            "Low acute risk.",
            "functioning_level": "Moderate — maintains employment and relationships "
            "but reports impairment during high-stress periods.",
        },
        "plan": {
            "interventions_used": [
                "CBT cognitive restructuring",
                "Guided progressive muscle relaxation",
            ],
            "homework_assignments": [
                "Practice PMR before bed nightly",
                "Complete thought record for 3 anxious episodes",
            ],
            "next_steps": [
                "Review thought records next session",
                "Introduce exposure hierarchy for workplace anxiety",
            ],
            "next_session": "One week, same time.",
        },
    }


def test_all_subfields_preserved(full_soap_json: dict[str, Any]) -> None:
    """Full JSON input produces structured SOAPNote with all sub-fields present."""
    result = _coerce_content_to_soap_note(full_soap_json)

    assert result.subjective.chief_complaint.text == "Increased anxiety related to work stress."
    assert result.subjective.mood_affect.text == "Anxious, restless, but engaged."
    assert result.subjective.symptoms is not None
    assert [s.text for s in result.subjective.symptoms] == [
        "Difficulty sleeping",
        "Racing thoughts",
        "Muscle tension",
    ]
    assert "breathing exercises" in result.subjective.client_narrative.text

    assert result.objective.appearance.text == "Well-groomed, casually dressed."
    assert "fidgeted" in result.objective.behavior.text
    assert result.objective.thought_process.text == "Linear and goal-directed."

    assert "Generalized Anxiety" in result.assessment.clinical_impression.text
    assert "No suicidal" in result.assessment.risk_assessment.text

    assert result.plan.interventions_used is not None
    assert [s.text for s in result.plan.interventions_used] == [
        "CBT cognitive restructuring",
        "Guided progressive muscle relaxation",
    ]
    assert result.plan.next_session.text == "One week, same time."

    narrative = result.to_narrative()

    assert "**Chief Complaint:**" in narrative["subjective"]
    assert "Increased anxiety" in narrative["subjective"]
    assert "**Mood/Affect:**" in narrative["subjective"]
    assert "**Symptoms:**" in narrative["subjective"]
    assert "- Difficulty sleeping" in narrative["subjective"]
    assert "- Racing thoughts" in narrative["subjective"]
    assert "- Muscle tension" in narrative["subjective"]
    assert "**Client Narrative:**" in narrative["subjective"]

    assert "**Appearance:**" in narrative["objective"]
    assert "**Behavior:**" in narrative["objective"]
    assert "**Speech:**" in narrative["objective"]
    assert "**Thought Process:**" in narrative["objective"]
    assert "**Affect Observed:**" in narrative["objective"]

    assert "**Clinical Impression:**" in narrative["assessment"]
    assert "**Progress:**" in narrative["assessment"]
    assert "**Risk Assessment:**" in narrative["assessment"]
    assert "**Functioning Level:**" in narrative["assessment"]

    assert "**Interventions Used:**" in narrative["plan"]
    assert "- CBT cognitive restructuring" in narrative["plan"]
    assert "**Homework Assignments:**" in narrative["plan"]
    assert "**Next Steps:**" in narrative["plan"]
    assert "**Next Session:**" in narrative["plan"]
    assert "One week" in narrative["plan"]


def test_missing_optional_fields_no_empty_headers() -> None:
    """Only required fields present — no empty headers in narrative output."""
    minimal_json: dict[str, Any] = {
        "subjective": {"chief_complaint": "Feels sad."},
        "objective": {"behavior": "Withdrawn, minimal eye contact."},
        "assessment": {
            "clinical_impression": "Major Depressive Disorder.",
            "risk_assessment": "Denies SI/HI.",
        },
        "plan": {"next_steps": ["Continue current medication."]},
    }
    result = _coerce_content_to_soap_note(minimal_json)
    narrative = result.to_narrative()

    assert "**Chief Complaint:**" in narrative["subjective"]
    assert "Feels sad" in narrative["subjective"]

    assert "**Mood/Affect:**" not in narrative["subjective"]
    assert "**Symptoms:**" not in narrative["subjective"]
    assert "**Client Narrative:**" not in narrative["subjective"]

    assert "**Appearance:**" not in narrative["objective"]
    assert "**Speech:**" not in narrative["objective"]
    assert "**Thought Process:**" not in narrative["objective"]
    assert "**Affect Observed:**" not in narrative["objective"]

    assert "**Progress:**" not in narrative["assessment"]
    assert "**Functioning Level:**" not in narrative["assessment"]

    assert "**Interventions Used:**" not in narrative["plan"]
    assert "**Homework Assignments:**" not in narrative["plan"]
    assert "**Next Session:**" not in narrative["plan"]


def test_empty_values_produce_no_artifacts() -> None:
    """Empty strings and empty lists don't produce headers or bullet artifacts."""
    empty_json: dict[str, Any] = {
        "subjective": {
            "chief_complaint": "Anxiety.",
            "mood_affect": "",
            "symptoms": [],
            "client_narrative": "   ",
        },
        "objective": {"behavior": "Cooperative.", "speech": "", "appearance": None},
        "assessment": {
            "clinical_impression": "GAD.",
            "risk_assessment": "Low risk.",
            "progress": "",
            "functioning_level": None,
        },
        "plan": {
            "next_steps": ["Follow up in 2 weeks."],
            "interventions_used": [],
            "homework_assignments": ["", "  "],
            "next_session": "",
        },
    }
    result = _coerce_content_to_soap_note(empty_json)
    narrative = result.to_narrative()

    assert "**Mood/Affect:**" not in narrative["subjective"]
    assert "**Symptoms:**" not in narrative["subjective"]
    assert "**Client Narrative:**" not in narrative["subjective"]

    assert "**Appearance:**" not in narrative["objective"]
    assert "**Speech:**" not in narrative["objective"]

    assert "**Progress:**" not in narrative["assessment"]
    assert "**Functioning Level:**" not in narrative["assessment"]

    assert "**Interventions Used:**" not in narrative["plan"]
    assert "**Homework Assignments:**" not in narrative["plan"]
    assert "**Next Session:**" not in narrative["plan"]

    assert "**Chief Complaint:**" in narrative["subjective"]
    assert "**Behavior:**" in narrative["objective"]
    assert "**Clinical Impression:**" in narrative["assessment"]
    assert "**Risk Assessment:**" in narrative["assessment"]
    assert "**Next Steps:**" in narrative["plan"]


def test_risk_assessment_always_in_assessment() -> None:
    """Risk assessment (legally required) appears in the Assessment section."""
    json_with_risk: dict[str, Any] = {
        "subjective": {"chief_complaint": "Feeling better."},
        "objective": {"behavior": "Engaged."},
        "assessment": {
            "risk_assessment": "No suicidal ideation. No homicidal ideation. Low risk.",
        },
        "plan": {"next_steps": ["Continue treatment."]},
    }
    result = _coerce_content_to_soap_note(json_with_risk)
    narrative = result.to_narrative()

    assert "**Risk Assessment:**" in narrative["assessment"]
    assert "No suicidal ideation" in narrative["assessment"]
    assert "Low risk" in narrative["assessment"]


def test_list_formatting_as_bullets() -> None:
    """List fields (symptoms, interventions, homework, next_steps) use bullet format."""
    json_with_lists: dict[str, Any] = {
        "subjective": {
            "chief_complaint": "Stress.",
            "symptoms": ["Insomnia", "Irritability", "Fatigue"],
        },
        "objective": {"behavior": "Calm."},
        "assessment": {
            "clinical_impression": "Adjustment disorder.",
            "risk_assessment": "Low.",
        },
        "plan": {
            "interventions_used": ["Psychoeducation", "Motivational interviewing"],
            "homework_assignments": ["Journal daily", "Exercise 3x/week"],
            "next_steps": ["Reassess medication", "Family session"],
        },
    }
    result = _coerce_content_to_soap_note(json_with_lists)
    narrative = result.to_narrative()

    assert "- Insomnia" in narrative["subjective"]
    assert "- Irritability" in narrative["subjective"]
    assert "- Fatigue" in narrative["subjective"]

    assert "- Psychoeducation" in narrative["plan"]
    assert "- Motivational interviewing" in narrative["plan"]

    assert "- Journal daily" in narrative["plan"]
    assert "- Exercise 3x/week" in narrative["plan"]

    assert "- Reassess medication" in narrative["plan"]
    assert "- Family session" in narrative["plan"]


def test_completely_empty_sections() -> None:
    """Missing sections produce empty strings in narrative, not errors."""
    result = _coerce_content_to_soap_note({})
    narrative = result.to_narrative()

    assert narrative["subjective"] == ""
    assert narrative["objective"] == ""
    assert narrative["assessment"] == ""
    assert narrative["plan"] == ""


def test_returns_soap_note_dataclass(full_soap_json: dict[str, Any]) -> None:
    """Conversion returns a SOAPNote dataclass instance."""
    result = _coerce_content_to_soap_note(full_soap_json)
    assert isinstance(result, SOAPNote)


class TestMockNoteGenerationService:
    """Tests for MockNoteGenerationService output format."""

    def test_mock_returns_subfield_headers(self) -> None:
        mock_service = MockNoteGenerationService()
        patient = Patient(
            id="p1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            diagnosis="Generalized Anxiety Disorder",
        )
        transcript = Transcript(format="txt", content="Sample transcript.")
        result = mock_service.generate_note(
            "soap", transcript, patient, datetime.fromisoformat("2024-06-01T00:00:00+00:00")
        )
        assert result.soap_note is not None
        narrative = result.soap_note.to_narrative()

        assert "**Chief Complaint:**" in narrative["subjective"]
        assert "**Mood/Affect:**" in narrative["subjective"]
        assert "**Symptoms:**" in narrative["subjective"]
        assert "**Client Narrative:**" in narrative["subjective"]

        assert "**Appearance:**" in narrative["objective"]
        assert "**Behavior:**" in narrative["objective"]
        assert "**Speech:**" in narrative["objective"]
        assert "**Thought Process:**" in narrative["objective"]
        assert "**Affect Observed:**" in narrative["objective"]

        assert "**Clinical Impression:**" in narrative["assessment"]
        assert "**Progress:**" in narrative["assessment"]
        assert "**Risk Assessment:**" in narrative["assessment"]
        assert "**Functioning Level:**" in narrative["assessment"]

        assert "**Interventions Used:**" in narrative["plan"]
        assert "**Homework Assignments:**" in narrative["plan"]
        assert "**Next Steps:**" in narrative["plan"]
        assert "**Next Session:**" in narrative["plan"]

    def test_mock_includes_diagnosis_in_output(self) -> None:
        mock_service = MockNoteGenerationService()
        patient = Patient(
            id="p1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            diagnosis="PTSD",
        )
        transcript = Transcript(format="txt", content="Sample.")
        result = mock_service.generate_note(
            "soap", transcript, patient, datetime.fromisoformat("2024-06-01T00:00:00+00:00")
        )
        assert result.soap_note is not None
        narrative = result.soap_note.to_narrative()

        assert "PTSD" in narrative["subjective"]
        assert "PTSD" in narrative["assessment"]

    def test_mock_risk_assessment_present(self) -> None:
        mock_service = MockNoteGenerationService()
        patient = Patient(
            id="p1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        )
        transcript = Transcript(format="txt", content="Sample.")
        result = mock_service.generate_note(
            "soap", transcript, patient, datetime.fromisoformat("2024-06-01T00:00:00+00:00")
        )
        assert result.soap_note is not None
        narrative = result.soap_note.to_narrative()

        assert "**Risk Assessment:**" in narrative["assessment"]


class TestRegistryGeneration:
    """End-to-end generation driven off the registry, with a fake gateway."""

    @pytest.fixture
    def isolated_registry(self) -> NoteTypeRegistry:
        reg = NoteTypeRegistry()
        register_builtin_note_types(reg)
        return reg

    @pytest.fixture
    def patient(self) -> Patient:
        return Patient(
            id="p1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            diagnosis="Adjustment disorder",
        )

    def test_narrative_routes_through_registry(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        """Narrative generation composes a registry-driven prompt and returns
        the LLM JSON coerced back into the registry shape."""
        transcript = Transcript(
            format="txt",
            content=(
                "[00:00] Therapist: How was your week?\n"
                "[00:05] Client: Better. I used the breathing exercise twice."
            ),
        )
        llm_data = {
            "note": {
                "body": (
                    "Client reports an improved week with partial use of "
                    "previously-taught breathing exercises. Engaged and "
                    "oriented throughout the session."
                )
            }
        }
        gateway = FakeStructuredLLMGateway(responses=[StructuredCompletion(data=llm_data)])
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)
        result = service.generate_note(
            "narrative",
            transcript,
            patient,
            datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
        )

        assert isinstance(result, GeneratedNote)
        assert result.note_type == "narrative"
        assert result.soap_note is None
        assert result.content == llm_data

        # Prompt is composed from the registry — contains the narrative
        # field's ai_hint.
        assert len(gateway.calls) == 1
        call = gateway.calls[0]
        assert "narrative summary of the session" in call["user_prompt"].lower()
        # Schema reflects the registry shape (section → field).
        assert call["response_schema"]["properties"]["note"]["properties"]["body"] == {
            "type": "string"
        }

    def test_soap_uses_prompt_builder_hook(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        """SOAP routes through the same gateway path but uses the
        hand-tuned prompt from prompts/soap.py instead of the auto-built
        one. The hand-tuned prompt is recognizable by its 'SOAP Note
        Structure' heading; the registry default has 'Section ... Fields'."""
        transcript = Transcript(format="txt", content="[00:00] Therapist: Hi.\n[00:01] Client: Hi.")
        soap_llm_data = {
            "subjective": {
                "chief_complaint": "Greeting only.",
                "mood_affect": "Neutral.",
                "symptoms": [],
                "client_narrative": "Greeting exchange.",
            },
            "objective": {
                "appearance": "",
                "behavior": "Brief.",
                "speech": "",
                "thought_process": "",
                "affect_observed": "",
            },
            "assessment": {
                "clinical_impression": "Insufficient content.",
                "progress": "",
                "risk_assessment": "No risk indicators present.",
                "functioning_level": "",
            },
            "plan": {
                "interventions_used": [],
                "homework_assignments": [],
                "next_steps": ["Continue treatment."],
                "next_session": "",
            },
        }
        # First call: SOAP generation. Second call: source-attribution
        # (Call-2). We return an empty attribution map — coverage tests
        # for the attribution parsing live in test_source_attribution.
        gateway = FakeStructuredLLMGateway(
            responses=[
                StructuredCompletion(data=soap_llm_data),
                StructuredCompletion(data={}),
            ]
        )
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)
        result = service.generate_note(
            "soap",
            transcript,
            patient,
            datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
        )

        assert result.note_type == "soap"
        assert result.soap_note is not None
        assert result.soap_note.subjective.chief_complaint.text == "Greeting only."

        # First call used the SOAP-specific prompt (hand-tuned).
        soap_call = gateway.calls[0]
        assert "SOAP Note Structure" in soap_call["user_prompt"]
        # Second call is Call-2 source attribution.
        attribution_call = gateway.calls[1]
        assert "claim" in attribution_call["user_prompt"].lower()

    def test_source_attribution_uses_own_budget_and_capped_thinking(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        """Call-2 (source attribution) must run with its own larger output
        budget AND an explicit thinking cap. Regression for the production
        failure where a thinking model exhausted the shared 8192 budget on
        reasoning and truncated with zero output, silently dropping source
        links on long transcripts."""
        transcript = Transcript(format="txt", content="[00:00] Client: hello")
        soap_llm_data = {
            "subjective": {
                "chief_complaint": "Greeting only.",
                "mood_affect": "Neutral.",
                "symptoms": [],
                "client_narrative": "Greeting exchange.",
            },
            "objective": {
                "appearance": "",
                "behavior": "Brief.",
                "speech": "",
                "thought_process": "",
                "affect_observed": "",
            },
            "assessment": {
                "clinical_impression": "Insufficient content.",
                "progress": "",
                "risk_assessment": "No risk indicators present.",
                "functioning_level": "",
            },
            "plan": {
                "interventions_used": [],
                "homework_assignments": [],
                "next_steps": ["Continue treatment."],
                "next_session": "",
            },
        }
        gateway = FakeStructuredLLMGateway(
            responses=[
                StructuredCompletion(data=soap_llm_data),
                StructuredCompletion(data={}),
            ]
        )
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)
        service.generate_note(
            "soap",
            transcript,
            patient,
            datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
        )

        settings = get_settings()
        attribution_call = gateway.calls[1]
        # Its own (larger) output budget, not the note-generation budget.
        assert (
            attribution_call["max_output_tokens"]
            == settings.note_source_attribution_max_output_tokens
        )
        # Thinking is explicitly capped so reasoning can't eat the whole budget.
        assert (
            attribution_call["thinking_budget"] == settings.note_source_attribution_thinking_budget
        )
        # And the cap leaves real room for the mapping to emit.
        assert attribution_call["max_output_tokens"] > attribution_call["thinking_budget"]

    def test_unknown_note_type_raises(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        service = RegistryNoteGenerationService(registry=isolated_registry)
        transcript = Transcript(format="txt", content="x")
        with pytest.raises(KeyError):
            service.generate_note(
                "does-not-exist",
                transcript,
                patient,
                datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
            )

    def test_truncated_output_retries_at_double_budget(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        """A first-attempt truncation (thinking model ate the output budget on
        a real transcript) retries once at twice the budget and succeeds — the
        regression for the SOAP 'Failed to generate' failure."""
        transcript = Transcript(format="txt", content="[00:00] Client: hello")
        narrative = {"note": {"body": "Client greeted the therapist."}}
        gateway = FakeStructuredLLMGateway(
            responses=[
                StructuredOutputTruncatedError("hit max_output_tokens"),
                StructuredCompletion(data=narrative),
            ]
        )
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)

        result = service.generate_note(
            "narrative",
            transcript,
            patient,
            datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
        )

        assert result.content == narrative
        # Two attempts: the retry doubled the output budget.
        assert len(gateway.calls) == 2
        assert gateway.calls[1]["max_output_tokens"] == 2 * gateway.calls[0]["max_output_tokens"]

    def test_truncated_output_twice_raises_value_error(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        """If even the doubled budget truncates, generation fails loud as a
        ValueError (the caller marks the session FAILED) rather than surfacing
        a confusing JSONDecodeError."""
        transcript = Transcript(format="txt", content="[00:00] Client: hello")
        gateway = FakeStructuredLLMGateway(
            responses=[
                StructuredOutputTruncatedError("truncated #1"),
                StructuredOutputTruncatedError("truncated #2"),
            ]
        )
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)

        with pytest.raises(ValueError, match="Note generation failed"):
            service.generate_note(
                "narrative",
                transcript,
                patient,
                datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
            )
        assert len(gateway.calls) == 2

    def test_mock_narrative_returns_registry_shape(self, patient: Patient) -> None:
        reg = NoteTypeRegistry()
        register_builtin_note_types(reg)
        service = MockNoteGenerationService(registry=reg)
        transcript = Transcript(format="txt", content="x")

        result = service.generate_note(
            "narrative",
            transcript,
            patient,
            datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
        )

        assert result.note_type == "narrative"
        assert result.soap_note is None
        assert "note" in result.content
        assert "body" in result.content["note"]
        assert result.content["note"]["body"]

    def test_new_note_type_works_without_code_changes(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        """Extensibility proof: registering a fresh definition routes
        through the existing pipeline with zero code changes outside
        this test. Models the future DAP / BIRP onboarding shape.
        """
        dap_like = NoteTypeDefinition(
            key="dap_test",
            label="DAP (test)",
            description="Data / Assessment / Plan — used to exercise the registry path.",
            sections=(
                NoteSectionDef(
                    key="data",
                    label="Data",
                    fields=(
                        NoteFieldDef(
                            key="objective_description",
                            label="Objective Description",
                            kind="text",
                            ai_hint="Observable facts from the session.",
                        ),
                    ),
                ),
                NoteSectionDef(
                    key="assessment",
                    label="Assessment",
                    fields=(
                        NoteFieldDef(
                            key="clinical_findings",
                            label="Clinical Findings",
                            kind="text",
                            ai_hint="Clinician's interpretation of the data.",
                        ),
                    ),
                ),
                NoteSectionDef(
                    key="plan",
                    label="Plan",
                    fields=(
                        NoteFieldDef(
                            key="next_steps",
                            label="Next Steps",
                            kind="list",
                            ai_hint="Planned interventions for next session.",
                        ),
                    ),
                ),
            ),
        )
        isolated_registry.register(dap_like)
        # Sanity-check we didn't accidentally collide with a builtin:
        assert isolated_registry.has("dap_test")
        assert isolated_registry.has(NARRATIVE_DEFINITION.key)

        gateway = FakeStructuredLLMGateway(
            responses=[
                StructuredCompletion(
                    data={
                        "data": {"objective_description": "Client arrived on time."},
                        "assessment": {"clinical_findings": "Engaged and oriented."},
                        "plan": {"next_steps": ["Continue weekly cadence."]},
                    }
                )
            ]
        )
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)
        result = service.generate_note(
            "dap_test",
            Transcript(format="txt", content="[00:00] Therapist: Hi.\n[00:01] Client: Hi."),
            patient,
            datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
        )

        assert result.note_type == "dap_test"
        assert result.soap_note is None
        assert result.content == {
            "data": {"objective_description": "Client arrived on time."},
            "assessment": {"clinical_findings": "Engaged and oriented."},
            "plan": {"next_steps": ["Continue weekly cadence."]},
        }


class TestTransientLLMDetection:
    """The transient-vs-deterministic discrimination that lets a rate-limited
    note-gen retry instead of failing the session."""

    def test_429_in_message_is_transient(self) -> None:
        assert _is_transient_llm_error(
            RuntimeError("Structured LLM call failed: 429 RESOURCE_EXHAUSTED")
        )

    def test_transient_marker_via_chained_cause(self) -> None:
        # The gateway flattens the provider error into a RuntimeError but chains
        # the original 429 via ``from exc`` — detection must walk the chain.
        wrapper = ValueError("Note generation failed: ...")
        wrapper.__cause__ = RuntimeError("Too Many Requests")
        assert _is_transient_llm_error(wrapper)

    def test_code_attribute_is_transient(self) -> None:
        class RateLimitedError(Exception):
            code = 429

        assert _is_transient_llm_error(RateLimitedError())

    def test_deterministic_errors_are_not_transient(self) -> None:
        assert not _is_transient_llm_error(ValueError("LLM returned invalid JSON: boom"))
        assert not _is_transient_llm_error(
            RuntimeError("Structured LLM call failed: schema mismatch")
        )


class TestGenerateNoteTransientHandling:
    """A transient gateway failure surfaces as ``TransientNoteGenerationError``;
    a deterministic one stays a plain ``ValueError``."""

    _TXN = Transcript(format="txt", content="[00:00] Therapist: How was your week?")
    _WHEN = datetime.fromisoformat("2024-06-01T00:00:00+00:00")

    @pytest.fixture
    def isolated_registry(self) -> NoteTypeRegistry:
        reg = NoteTypeRegistry()
        register_builtin_note_types(reg)
        return reg

    @pytest.fixture
    def patient(self) -> Patient:
        return Patient(
            id="p1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            diagnosis="Adjustment disorder",
        )

    def test_transient_gateway_error_raises_transient(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        gateway = FakeStructuredLLMGateway(
            responses=[RuntimeError("Structured LLM call failed: 429 RESOURCE_EXHAUSTED")]
        )
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)
        with pytest.raises(TransientNoteGenerationError):
            service.generate_note("narrative", self._TXN, patient, self._WHEN)

    def test_deterministic_gateway_error_is_not_transient(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        gateway = FakeStructuredLLMGateway(
            responses=[RuntimeError("Structured LLM call failed: schema mismatch")]
        )
        service = RegistryNoteGenerationService(registry=isolated_registry, llm_gateway=gateway)
        with pytest.raises(ValueError, match="Note generation failed") as exc_info:
            service.generate_note("narrative", self._TXN, patient, self._WHEN)
        assert not isinstance(exc_info.value, TransientNoteGenerationError)
