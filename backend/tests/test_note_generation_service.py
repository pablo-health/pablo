# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for note generation service (SOAP + registry-driven types)."""

import os

os.environ["ENVIRONMENT"] = "development"

from datetime import datetime
from typing import Any

import pytest
from app.models import Patient, SOAPNote, Transcript
from app.notes import NoteTypeRegistry, register_builtin_note_types
from app.services.note_generation_service import (
    GeneratedNote,
    MeetingTranscriptionNoteService,
    MockNoteGenerationService,
)


@pytest.fixture
def service() -> MeetingTranscriptionNoteService:
    """Create a MeetingTranscriptionNoteService for testing conversion."""
    return MeetingTranscriptionNoteService(therapist_name="Dr. Test")


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


def test_all_subfields_preserved(
    service: MeetingTranscriptionNoteService, full_soap_json: dict[str, Any]
) -> None:
    """Full JSON input produces structured SOAPNote with all sub-fields present."""
    result = service._convert_json_to_soap_note(full_soap_json)

    # Verify structured data is preserved (SOAPSentence .text)
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

    # Verify narrative rendering includes all sub-field headers
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


def test_missing_optional_fields_no_empty_headers(
    service: MeetingTranscriptionNoteService,
) -> None:
    """Only required fields present — no empty headers in narrative output."""
    minimal_json: dict[str, Any] = {
        "subjective": {
            "chief_complaint": "Feels sad.",
        },
        "objective": {
            "behavior": "Withdrawn, minimal eye contact.",
        },
        "assessment": {
            "clinical_impression": "Major Depressive Disorder.",
            "risk_assessment": "Denies SI/HI.",
        },
        "plan": {
            "next_steps": ["Continue current medication."],
        },
    }
    result = service._convert_json_to_soap_note(minimal_json)
    narrative = result.to_narrative()

    # Present fields
    assert "**Chief Complaint:**" in narrative["subjective"]
    assert "Feels sad" in narrative["subjective"]

    # Absent fields should NOT appear
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


def test_empty_values_produce_no_artifacts(
    service: MeetingTranscriptionNoteService,
) -> None:
    """Empty strings and empty lists don't produce headers or bullet artifacts."""
    empty_json: dict[str, Any] = {
        "subjective": {
            "chief_complaint": "Anxiety.",
            "mood_affect": "",
            "symptoms": [],
            "client_narrative": "   ",
        },
        "objective": {
            "behavior": "Cooperative.",
            "speech": "",
            "appearance": None,
        },
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
    result = service._convert_json_to_soap_note(empty_json)
    narrative = result.to_narrative()

    # Empty string / whitespace-only / None fields should be omitted
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

    # Valid fields are still present
    assert "**Chief Complaint:**" in narrative["subjective"]
    assert "**Behavior:**" in narrative["objective"]
    assert "**Clinical Impression:**" in narrative["assessment"]
    assert "**Risk Assessment:**" in narrative["assessment"]
    assert "**Next Steps:**" in narrative["plan"]


def test_risk_assessment_always_in_assessment(
    service: MeetingTranscriptionNoteService,
) -> None:
    """Risk assessment (legally required) appears in the Assessment section."""
    json_with_risk: dict[str, Any] = {
        "subjective": {"chief_complaint": "Feeling better."},
        "objective": {"behavior": "Engaged."},
        "assessment": {
            "risk_assessment": "No suicidal ideation. No homicidal ideation. Low risk.",
        },
        "plan": {"next_steps": ["Continue treatment."]},
    }
    result = service._convert_json_to_soap_note(json_with_risk)
    narrative = result.to_narrative()

    assert "**Risk Assessment:**" in narrative["assessment"]
    assert "No suicidal ideation" in narrative["assessment"]
    assert "Low risk" in narrative["assessment"]


def test_list_formatting_as_bullets(
    service: MeetingTranscriptionNoteService,
) -> None:
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
    result = service._convert_json_to_soap_note(json_with_lists)
    narrative = result.to_narrative()

    # Symptoms as bullets
    assert "- Insomnia" in narrative["subjective"]
    assert "- Irritability" in narrative["subjective"]
    assert "- Fatigue" in narrative["subjective"]

    # Interventions as bullets
    assert "- Psychoeducation" in narrative["plan"]
    assert "- Motivational interviewing" in narrative["plan"]

    # Homework as bullets
    assert "- Journal daily" in narrative["plan"]
    assert "- Exercise 3x/week" in narrative["plan"]

    # Next steps as bullets
    assert "- Reassess medication" in narrative["plan"]
    assert "- Family session" in narrative["plan"]


def test_completely_empty_sections(
    service: MeetingTranscriptionNoteService,
) -> None:
    """Missing sections produce empty strings in narrative, not errors."""
    result = service._convert_json_to_soap_note({})
    narrative = result.to_narrative()

    assert narrative["subjective"] == ""
    assert narrative["objective"] == ""
    assert narrative["assessment"] == ""
    assert narrative["plan"] == ""


def test_returns_soap_note_dataclass(
    service: MeetingTranscriptionNoteService, full_soap_json: dict[str, Any]
) -> None:
    """Conversion returns a SOAPNote dataclass instance."""
    result = service._convert_json_to_soap_note(full_soap_json)
    assert isinstance(result, SOAPNote)


class TestMockNoteGenerationService:
    """Tests for MockNoteGenerationService output format."""

    def test_mock_returns_subfield_headers(self) -> None:
        """Mock output includes sub-field markdown headers matching real format."""
        mock_service = MockNoteGenerationService()
        patient = Patient(
            id="p1",
            user_id="u1",
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
        narrative = result.soap_note.to_narrative()

        # Subjective headers
        assert "**Chief Complaint:**" in narrative["subjective"]
        assert "**Mood/Affect:**" in narrative["subjective"]
        assert "**Symptoms:**" in narrative["subjective"]
        assert "**Client Narrative:**" in narrative["subjective"]

        # Objective headers
        assert "**Appearance:**" in narrative["objective"]
        assert "**Behavior:**" in narrative["objective"]
        assert "**Speech:**" in narrative["objective"]
        assert "**Thought Process:**" in narrative["objective"]
        assert "**Affect Observed:**" in narrative["objective"]

        # Assessment headers
        assert "**Clinical Impression:**" in narrative["assessment"]
        assert "**Progress:**" in narrative["assessment"]
        assert "**Risk Assessment:**" in narrative["assessment"]
        assert "**Functioning Level:**" in narrative["assessment"]

        # Plan headers
        assert "**Interventions Used:**" in narrative["plan"]
        assert "**Homework Assignments:**" in narrative["plan"]
        assert "**Next Steps:**" in narrative["plan"]
        assert "**Next Session:**" in narrative["plan"]

    def test_mock_includes_diagnosis_in_output(self) -> None:
        """Mock includes patient diagnosis in the output."""
        mock_service = MockNoteGenerationService()
        patient = Patient(
            id="p1",
            user_id="u1",
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
        narrative = result.soap_note.to_narrative()

        assert "PTSD" in narrative["subjective"]
        assert "PTSD" in narrative["assessment"]

    def test_mock_risk_assessment_present(self) -> None:
        """Mock always includes risk assessment in Assessment section."""
        mock_service = MockNoteGenerationService()
        patient = Patient(
            id="p1",
            user_id="u1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        )
        transcript = Transcript(format="txt", content="Sample.")
        result = mock_service.generate_note(
            "soap", transcript, patient, datetime.fromisoformat("2024-06-01T00:00:00+00:00")
        )
        narrative = result.soap_note.to_narrative()

        assert "**Risk Assessment:**" in narrative["assessment"]


class TestNarrativeGeneration:
    """End-to-end narrative generation driven off the registry."""

    @pytest.fixture
    def isolated_registry(self) -> NoteTypeRegistry:
        reg = NoteTypeRegistry()
        register_builtin_note_types(reg)
        return reg

    @pytest.fixture
    def patient(self) -> Patient:
        return Patient(
            id="p1",
            user_id="u1",
            first_name="Jane",
            last_name="Doe",
            created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            updated_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
            diagnosis="Adjustment disorder",
        )

    def test_narrative_end_to_end_against_sample_transcript(
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

        llm_response = {
            "note": {
                "body": (
                    "Client reports an improved week with partial use of "
                    "previously-taught breathing exercises. Engaged and "
                    "oriented throughout the session."
                )
            }
        }

        captured: dict[str, Any] = {}

        class _FakeLLM:
            def call_structured(
                self,
                prompt: str,
                response_schema: dict[str, Any],
                **_: Any,
            ) -> dict[str, Any]:
                captured["prompt"] = prompt
                captured["schema"] = response_schema
                return llm_response

        service = MeetingTranscriptionNoteService(
            registry=isolated_registry,
            llm_client_factory=_FakeLLM,
        )
        result = service.generate_note(
            "narrative",
            transcript,
            patient,
            datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
        )

        assert isinstance(result, GeneratedNote)
        assert result.note_type == "narrative"
        assert result.soap_note is None
        assert result.content == {
            "note": {
                "body": (
                    "Client reports an improved week with partial use of "
                    "previously-taught breathing exercises. Engaged and "
                    "oriented throughout the session."
                )
            }
        }
        # Prompt is composed from the registry — contains the narrative field's ai_hint.
        assert "narrative summary of the session" in captured["prompt"].lower()
        # Schema reflects the registry shape (section → field).
        assert captured["schema"]["properties"]["note"]["properties"]["body"] == {"type": "string"}

    def test_unknown_note_type_raises(
        self, isolated_registry: NoteTypeRegistry, patient: Patient
    ) -> None:
        service = MeetingTranscriptionNoteService(registry=isolated_registry)
        transcript = Transcript(format="txt", content="x")
        with pytest.raises(KeyError):
            service.generate_note(
                "does-not-exist",
                transcript,
                patient,
                datetime.fromisoformat("2024-06-01T00:00:00+00:00"),
            )

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
