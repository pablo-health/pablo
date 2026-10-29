# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Importing a note written in another records system as any note type.

The definition under test is the psychiatric follow-up template a practice
starts from in Settings, loaded the way ``test_note_type_templates`` loads it,
and the document is an authored transfer note in a prescriber's layout
(``fixtures/notes/transfer_psychiatric_follow_up.txt``; the client is
invented). The model is the fake gateway, answering as a faithful relocation
would, so these pin the contract around it: the type's own schema and fields
go out, its shape comes back, every stated field is checked against the
document, and anything the document does not say is flagged.
"""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest
from app.notes import NoteTypeDefinition, get_default_registry, register_builtin_note_types
from app.notes.practice_types import PracticeNoteTypeSpec, to_definition
from app.notes.spec_templates import TEMPLATES_DIR
from app.services.note_import_service import EXTRACT_SYSTEM_PROMPT, NoteImportService
from app.services.structured_llm_gateway import FakeStructuredLLMGateway, StructuredCompletion

_TEMPLATE = TEMPLATES_DIR / "psychiatric_follow_up.json"
_DOCUMENT = (
    Path(__file__).parent / "fixtures/notes/transfer_psychiatric_follow_up.txt"
).read_text()

# Every field the document states, quoted from it; the ones it is silent on
# (cannabis, PDMP, labs, the psychotherapy section) left empty.
_RELOCATED: dict[str, Any] = {
    "encounter": {
        "visit_details": "Telehealth video visit, 25 minutes. Client located at home; "
        "provider located in clinic office.",
        "place_of_service": "Telehealth video visit",
    },
    "subjective": {
        "chief_complaint": "The new dose is helping but I still wake up at 4 a.m.",
        "depression": "Since the last visit in July, Jordan reports mood is steadier. Energy fair.",
        "anxiety": "No panic attacks in the past month.",
        "insomnia_sleep": "Early-morning waking persists three to four nights a week.",
        "inattention_hyperactivity": "",
        "mania": "Denies manic symptoms.",
        "appetite_eating": "Appetite normal.",
        "onset_duration_course": "",
        "recent_stressors": "",
        "functioning": "they have returned to full-time work.",
        "adherence": "Taking sertraline daily as prescribed; missed two doses while traveling.",
        "side_effects": "Mild nausea in the first week after the increase, now resolved. "
        "Denies sexual side effects.",
    },
    "substance_use": {
        "alcohol": "two glasses of wine per week.",
        "tobacco_nicotine": "Tobacco: none.",
        "cannabis": "",
        "other_substances": "",
    },
    "medications": {
        "current_medications": [
            "Sertraline 100 mg by mouth daily",
            "Hydroxyzine 25 mg by mouth at bedtime as needed for sleep",
        ],
        "allergies": "Penicillin (rash).",
    },
    "risk": {
        "suicidal_homicidal_ideation": "Denies suicidal ideation, intent, or plan. "
        "Denies homicidal ideation.",
        "self_harm_violence": "",
        "risk_protective_factors": "Protective factors: employed, close relationship with "
        "sister, engaged in treatment.",
        "overall_risk": "Overall acute risk: low.",
        "safety_plan": "",
    },
    "mse": {
        "appearance_behavior": "casually dressed, good eye contact.",
        "orientation": "alert and oriented to person, place, time, and situation.",
        "speech": "normal rate and volume.",
        "mood_affect": 'Mood: "better." Affect: mildly anxious, reactive.',
        "thought_process": "linear and goal-directed.",
        "thought_content": "no delusions; denies hallucinations.",
        "cognition": "grossly intact.",
        "insight_judgment": "good.",
    },
    "measures": {"measures_reviewed": "PHQ-9: 8 (was 14 in July). GAD-7: 9."},
    "assessment": {
        "diagnoses": [
            {
                "label": "Major depressive disorder, recurrent, moderate",
                "code": "F33.1",
                "status": "improving",
            },
            {"label": "Generalized anxiety disorder", "code": "F41.1", "status": None},
        ],
        "formulation": "Mood and anxiety improving on sertraline; residual early-morning insomnia.",
        "medical_necessity": "",
    },
    "mdm": {"problems_addressed": "", "data_reviewed": "", "management_risk": ""},
    "plan": {
        "medication_plan": [
            "Continue sertraline 100 mg daily.",
            "Continue hydroxyzine 25 mg at bedtime as needed.",
        ],
        "pdmp": "",
        "informed_consent": "",
        "education_provided": [],
        "lifestyle_counseling": [],
        "labs": "",
        "referrals_coordination": "",
        "follow_up": "Return in 6 weeks, sooner if needed.",
        "emergency_instructions": "Call 988 or go to the nearest emergency room if thoughts "
        "of self-harm occur.",
    },
    "psychotherapy": {
        "psychotherapy_time": "",
        "issues_addressed": "",
        "modality_interventions": "",
        "response": "",
        "goal_plan": "",
        "progress": "",
        "therapy_cadence": "",
    },
}


@pytest.fixture(autouse=True)
def _builtins() -> None:
    register_builtin_note_types(get_default_registry())


@pytest.fixture
def follow_up() -> NoteTypeDefinition:
    spec = PracticeNoteTypeSpec.model_validate(json.loads(_TEMPLATE.read_text())["spec"])
    return to_definition("custom.psychiatric_follow_up", 1, spec)


def _reply(content: dict[str, Any], **extra: Any) -> FakeStructuredLLMGateway:
    data = {**content, "session_date": "2026-08-19", "session_time": "14:30", **extra}
    return FakeStructuredLLMGateway(default_response=StructuredCompletion(data=data))


def _stated_paths(content: dict[str, Any]) -> set[str]:
    paths: set[str] = set()
    for section, fields in content.items():
        for key, value in fields.items():
            if isinstance(value, list):
                paths.update(f"{section}.{key}[{i}]" for i in range(len(value)))
            elif value:
                paths.add(f"{section}.{key}")
    return paths


def test_the_note_comes_back_in_exactly_the_types_shape(
    follow_up: NoteTypeDefinition,
) -> None:
    parsed = NoteImportService(llm_gateway=_reply(_RELOCATED)).parse_note(_DOCUMENT, follow_up)

    assert list(parsed.content) == follow_up.section_keys()
    for section in follow_up.sections:
        assert list(parsed.content[section.key]) == section.field_keys()
    # The registry shape a generated note of this type has, not SOAP's sentences.
    assert parsed.content["subjective"]["chief_complaint"] == (
        "The new dose is helping but I still wake up at 4 a.m."
    )
    assert parsed.content["assessment"]["diagnoses"][0] == {
        "label": "Major depressive disorder, recurrent, moderate",
        "code": "F33.1",
        "status": "improving",
    }
    assert parsed.session_date == date(2026, 8, 19)
    assert parsed.session_time == time(14, 30)


def test_every_field_the_document_states_is_grounded(follow_up: NoteTypeDefinition) -> None:
    parsed = NoteImportService(llm_gateway=_reply(_RELOCATED)).parse_note(_DOCUMENT, follow_up)

    assert {g.path for g in parsed.grounding} == _stated_paths(_RELOCATED)
    assert parsed.ungrounded == ()


def test_a_field_the_document_never_states_is_flagged(follow_up: NoteTypeDefinition) -> None:
    invented = json.loads(json.dumps(_RELOCATED))
    invented["plan"]["pdmp"] = "State prescription monitoring database reviewed; no early fills."

    parsed = NoteImportService(llm_gateway=_reply(invented)).parse_note(_DOCUMENT, follow_up)

    assert [g.path for g in parsed.ungrounded] == ["plan.pdmp"]


def test_the_model_is_asked_for_the_types_fields_from_the_document(
    follow_up: NoteTypeDefinition,
) -> None:
    gateway = _reply(_RELOCATED)
    NoteImportService(llm_gateway=gateway).parse_note(_DOCUMENT, follow_up)

    (call,) = gateway.calls
    assert set(call["response_schema"]["properties"]) == {
        *follow_up.section_keys(),
        "session_date",
        "session_time",
        "signed_by",
        "note_inputs",
    }
    assert call["user_prompt"].startswith("# Source note")
    assert "existing clinical note" in call["user_prompt"]
    assert _DOCUMENT in call["user_prompt"]
    assert "- pdmp (text): PDMP" in call["user_prompt"]
    assert "session_date" in call["user_prompt"]
    # Extraction rules, not the type's own drafting prompt.
    assert follow_up.label in call["system_prompt"]
    assert "VERBATIM" in call["system_prompt"]
    assert "Never fabricate text" in call["system_prompt"]
    assert follow_up.system_prompt is not None
    assert follow_up.system_prompt not in call["system_prompt"]
    assert call["temperature"] == 0.0


def test_soap_stays_the_default_with_its_own_prompt() -> None:
    gateway = FakeStructuredLLMGateway(default_response=StructuredCompletion(data={}))
    NoteImportService(llm_gateway=gateway).parse_note("Subjective: slept better.")

    (call,) = gateway.calls
    assert call["system_prompt"] == EXTRACT_SYSTEM_PROMPT
    assert "existing SOAP note" in call["user_prompt"]
    assert {"subjective", "objective", "assessment", "plan"} <= set(
        call["response_schema"]["properties"]
    )


# --- The type's declared inputs, and who signed it ----------------------------------

_STATED_INPUTS = {
    "place_of_service": "Telehealth",
    "client_location": "home",
    "provider_location": "clinic office",
}


def test_the_documents_place_of_service_fills_the_notes_input(
    follow_up: NoteTypeDefinition,
) -> None:
    gateway = _reply(_RELOCATED, note_inputs=_STATED_INPUTS)

    parsed = NoteImportService(llm_gateway=gateway).parse_note(_DOCUMENT, follow_up)

    assert parsed.note_inputs == _STATED_INPUTS
    (call,) = gateway.calls
    # One string per declared input; the MDM review's choices stay the clinician's.
    asked = call["response_schema"]["properties"]["note_inputs"]["properties"]
    assert set(asked) == {"place_of_service", "client_location", "provider_location"}
    assert (
        '- place_of_service: Place of service (one of "Telehealth" / "In office")'
        in (call["user_prompt"])
    )
    assert "only when the document plainly states it" in call["user_prompt"]


def test_a_choice_is_read_as_the_declared_option_and_anything_else_is_dropped(
    follow_up: NoteTypeDefinition,
) -> None:
    for stated, kept in [("telehealth ", "Telehealth"), ("Video visit", None), ("", None)]:
        gateway = _reply(
            _RELOCATED,
            note_inputs={
                "place_of_service": stated,
                # Not in the document: a location the model made up is not kept.
                "client_location": "a hotel in another state",
                # The MDM review's choice is never read from a document.
                "mdm_risk": "low",
            },
        )

        parsed = NoteImportService(llm_gateway=gateway).parse_note(_DOCUMENT, follow_up)

        assert parsed.note_inputs == ({"place_of_service": kept} if kept else {})


def test_a_document_that_does_not_say_leaves_the_input_blank(
    follow_up: NoteTypeDefinition,
) -> None:
    silent = "\n".join(line for line in _DOCUMENT.splitlines() if not line.startswith("Visit:"))
    gateway = _reply(_RELOCATED, note_inputs={"place_of_service": ""})

    parsed = NoteImportService(llm_gateway=gateway).parse_note(silent, follow_up)

    # Required on the type, and still left unset: an import never guesses one.
    assert parsed.note_inputs == {}


def test_the_signer_is_kept_as_written_and_only_when_the_document_states_one(
    follow_up: NoteTypeDefinition,
) -> None:
    signed = _DOCUMENT + "\nElectronically signed by A. Rivera, PMHNP-BC on 08/19/2026"

    def signer(document: str, stated: str) -> str | None:
        gateway = _reply(_RELOCATED, signed_by=stated)
        return NoteImportService(llm_gateway=gateway).parse_note(document, follow_up).signed_by

    assert signer(signed, "A. Rivera, PMHNP-BC") == "A. Rivera, PMHNP-BC"
    # The fixture names no signer; a name the model supplies anyway is not kept.
    assert signer(_DOCUMENT, "") is None
    assert signer(_DOCUMENT, "Dr. Someone") is None
