# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the DAP, BIRP, GIRP, Meeting Summary, Intake, Safety Plan and
Treatment Plan definitions.

Pins the catalog contract: which keys ship, what tier they sit on,
which lifecycle context they use, and which transcript-derivable
fields carry an ``ai_hint`` versus which clinician/system fields are
deliberately blank (so the LLM never auto-populates risk, diagnosis,
consents, or signatures).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import Note, Patient, Transcript
from app.notes import (
    BIRP_DEFINITION,
    DAP_DEFINITION,
    GIRP_DEFINITION,
    INTAKE_DEFINITION,
    MEETING_SUMMARY_DEFINITION,
    SAFETY_PLAN_DEFINITION,
    TREATMENT_PLAN_DEFINITION,
    NoteTypeRegistry,
    register_builtin_note_types,
)
from app.notes.meeting_summary import (
    MEETING_SUMMARY_SYSTEM_PROMPT,
    build_meeting_summary_prompt,
)
from app.repositories import InMemoryNotesRepository
from app.services.chat_context_bundler import (
    SOURCE_KEY_MOST_RECENT_INTAKE,
    SOURCE_KEY_PROGRESS_NOTES_RECENT,
    SOURCE_KEY_SAFETY_PLAN_ACTIVE,
    SOURCE_KEY_TREATMENT_PLAN_ACTIVE,
    assemble_context_bundle,
)
from app.services.note_generation_service import RegistryNoteGenerationService
from app.services.structured_llm_gateway import (
    FakeStructuredLLMGateway,
    StructuredCompletion,
)

MOVED_KEYS = {
    "dap",
    "birp",
    "girp",
    "meeting_summary",
    "intake",
    "safety_plan",
    "treatment_plan",
}


def test_builtin_registration_includes_every_format() -> None:
    """Registering the built-ins installs each of these formats."""
    registry = NoteTypeRegistry()

    register_builtin_note_types(registry)

    assert set(registry.keys()) >= MOVED_KEYS


def test_session_definitions_are_core_session_tier() -> None:
    """DAP/BIRP/GIRP are session-context built-ins."""
    for definition in (DAP_DEFINITION, BIRP_DEFINITION, GIRP_DEFINITION):
        assert definition.tier == "core"
        assert definition.context == "session"


def test_meeting_summary_is_core_session_tier() -> None:
    """Meeting Summary is non-clinical: session context, generated from a
    recording's transcript."""
    assert MEETING_SUMMARY_DEFINITION.key == "meeting_summary"
    assert MEETING_SUMMARY_DEFINITION.tier == "core"
    assert MEETING_SUMMARY_DEFINITION.context == "session"


def test_meeting_summary_fields_all_have_ai_hints() -> None:
    """Every Meeting Summary field is transcript-derivable — none are
    clinician-form or system fields, so all carry an ai_hint."""
    for section in MEETING_SUMMARY_DEFINITION.sections:
        for f in section.fields:
            assert f.ai_hint, f"{section.key}.{f.key} is missing an ai_hint"


def test_meeting_summary_prompt_reframes_away_from_clinical() -> None:
    """The business-meeting framing lives in the system prompt; the user
    prompt carries the field enumeration, date, and transcript."""

    now = datetime(2026, 7, 27, tzinfo=UTC)
    patient = Patient(
        id="p-1",
        first_name="Biz",
        last_name="Meetings",
        created_at=now,
        updated_at=now,
    )
    transcript = Transcript(format="text", content="We agreed to pilot in August.")

    prompt = build_meeting_summary_prompt(MEETING_SUMMARY_DEFINITION, transcript, patient, now)

    assert "meeting minutes" in MEETING_SUMMARY_SYSTEM_PROMPT
    assert "patient or client" in MEETING_SUMMARY_SYSTEM_PROMPT

    assert "We agreed to pilot in August." in prompt
    assert "Meeting date: 2026-07-27" in prompt
    assert "NOT a therapy session" not in prompt
    # Every field key is enumerated so the structured response schema
    # and the prompt describe the same shape.
    for section in MEETING_SUMMARY_DEFINITION.sections:
        for f in section.fields:
            assert f.key in prompt


def test_meeting_summary_system_prompt_reaches_the_gateway() -> None:
    """``MEETING_SUMMARY_DEFINITION.system_prompt`` is wired all the way
    through ``RegistryNoteGenerationService`` into the LLM gateway call —
    not just set on the definition but actually used for generation."""

    registry = NoteTypeRegistry()
    registry.register(MEETING_SUMMARY_DEFINITION)

    gateway = FakeStructuredLLMGateway(
        responses=[
            StructuredCompletion(
                data={
                    "overview": {
                        "attendees": ["Alex (Acme)"],
                        "purpose": "Discuss a pilot.",
                        "summary": "We agreed to pilot in August.",
                    },
                    "outcomes": {
                        "key_points": ["Pilot in August"],
                        "decisions": ["Alex approved the pilot"],
                        "action_items": [],
                        "open_questions": [],
                    },
                    "follow_up": {
                        "next_meeting": "",
                    },
                }
            )
        ]
    )
    service = RegistryNoteGenerationService(registry=registry, llm_gateway=gateway)
    now = datetime(2026, 7, 27, tzinfo=UTC)
    patient = Patient(
        id="p-1",
        first_name="Biz",
        last_name="Meetings",
        created_at=now,
        updated_at=now,
    )
    transcript = Transcript(format="txt", content="We agreed to pilot in August.")

    service.generate_note("meeting_summary", transcript, patient, now)

    assert gateway.calls[0]["system_prompt"] == MEETING_SUMMARY_SYSTEM_PROMPT
    assert "NOT a therapy session" not in gateway.calls[0]["user_prompt"]


def test_intake_is_core_patient_tier() -> None:
    """Intake is patient-context (versioned per patient, not per session)."""
    assert INTAKE_DEFINITION.key == "intake"
    assert INTAKE_DEFINITION.tier == "core"
    assert INTAKE_DEFINITION.context == "patient"


def test_intake_has_required_sections() -> None:
    """The biopsychosocial schema (CMS MBPM Ch. 15 §160) sections are present."""
    assert INTAKE_DEFINITION.section_keys() == [
        "identification",
        "presenting",
        "history",
        "social",
        "mse",
        "risk",
        "strengths",
        "assessment",
        "plan",
        "consents",
        "signature",
    ]


def _intake_field(section_key: str, field_key: str):
    section = next(s for s in INTAKE_DEFINITION.sections if s.key == section_key)
    return next(f for f in section.fields if f.key == field_key)


def test_intake_transcript_fields_have_ai_hints() -> None:
    """Transcript-derivable fields (HPI, history, social, audible MSE) carry an ai_hint."""
    transcript_fields = [
        ("identification", "referral_reason"),
        ("presenting", "chief_complaint"),
        ("presenting", "history_of_present_illness"),
        ("history", "psychiatric_history"),
        ("history", "medical_history"),
        ("history", "current_medications"),
        ("history", "substance_use_history"),
        ("history", "family_history"),
        ("history", "developmental_history"),
        ("history", "trauma_history"),
        ("social", "social_history"),
        ("social", "relationships_supports"),
        ("mse", "speech"),
        ("mse", "mood_affect"),
        ("mse", "thought_process_content"),
        ("mse", "perception"),
        ("strengths", "strengths"),
        ("plan", "initial_recommendations"),
    ]

    for section_key, field_key in transcript_fields:
        field = _intake_field(section_key, field_key)
        assert field.ai_hint, f"{section_key}.{field_key} must have an ai_hint"


def test_intake_clinician_and_system_fields_have_no_ai_hint() -> None:
    """Risk, diagnosis, formulation, visual MSE, consents, signatures stay LLM-silent.

    The design rule (per ``docs/NOTE_TYPES_PRACTICE.md`` §Intake) is that the
    LLM must not infer risk language, premature diagnoses, or signed consents
    from a transcript that doesn't support them. Empty ``ai_hint`` is how we
    enforce that at the schema layer.
    """
    blank_fields = [
        ("identification", "datetime_of_service"),
        ("identification", "duration_minutes"),
        ("identification", "modality"),
        ("identification", "referral_source"),
        ("mse", "appearance_behavior"),
        ("mse", "cognition"),
        ("mse", "insight_judgment"),
        ("risk", "suicide_risk"),
        ("risk", "homicide_risk"),
        ("risk", "self_harm_risk"),
        ("risk", "abuse_neglect"),
        ("risk", "protective_factors"),
        ("assessment", "dsm5_diagnosis"),
        ("assessment", "case_formulation"),
        ("consents", "consent_to_treatment_signed"),
        ("consents", "roi_signed"),
        ("consents", "telehealth_consent_signed"),
        ("signature", "clinician_signature"),
        ("signature", "signed_at"),
        ("signature", "supervisor_cosignature_required"),
        ("signature", "supervisor_signature"),
        ("signature", "supervisor_signed_at"),
    ]

    for section_key, field_key in blank_fields:
        field = _intake_field(section_key, field_key)
        assert field.ai_hint == "", f"{section_key}.{field_key} must have empty ai_hint"


def test_intake_list_fields_use_list_kind() -> None:
    """``current_medications``, ``strengths``, ``dsm5_diagnosis`` are list-kind."""
    assert _intake_field("history", "current_medications").kind == "list"
    assert _intake_field("strengths", "strengths").kind == "list"
    assert _intake_field("assessment", "dsm5_diagnosis").kind == "list"


def _safety_plan_field(section_key: str, field_key: str):
    section = next(s for s in SAFETY_PLAN_DEFINITION.sections if s.key == section_key)
    return next(f for f in section.fields if f.key == field_key)


def test_safety_plan_is_core_patient_tier() -> None:
    """Safety Plan is patient-context (versioned per patient)."""
    assert SAFETY_PLAN_DEFINITION.key == "safety_plan"
    assert SAFETY_PLAN_DEFINITION.tier == "core"
    assert SAFETY_PLAN_DEFINITION.context == "patient"


def test_safety_plan_has_six_stanley_brown_steps() -> None:
    """All six Stanley-Brown steps are present in order, plus optional reasons-for-living
    and the status/acknowledgment/signature trailers."""
    assert SAFETY_PLAN_DEFINITION.section_keys() == [
        "warning_signs",
        "internal_coping",
        "distractions",
        "help_contacts",
        "professionals",
        "means_restriction",
        "reasons_for_living",
        "status",
        "acknowledgment",
        "signature",
    ]


def test_safety_plan_step_distinction_is_preserved() -> None:
    """Step 3 (distractions) and Step 4 (help) are kept separate — surveyors check this."""
    step3 = _safety_plan_field("distractions", "distracting_people_settings")
    step4 = _safety_plan_field("help_contacts", "help_people")
    assert step3.kind == "list"
    assert step4.kind == "list"
    assert "distraction" in step3.ai_hint.lower()
    assert "help" in step4.ai_hint.lower()


def test_safety_plan_means_restriction_fields_demand_specifics() -> None:
    """Means-restriction plan fields must signal that 'discussed' is not acceptable."""
    for plan_field_key in ("firearms_plan", "medications_plan", "other_means_plan"):
        field = _safety_plan_field("means_restriction", plan_field_key)
        assert field.ai_hint, f"{plan_field_key} must have an ai_hint"


def test_safety_plan_transcript_fields_have_ai_hints() -> None:
    """Stanley-Brown step fields the LLM may extract from a recording carry an ai_hint.

    Per the design doc, these source as ``transcript|clinician_form`` — the
    universal transcript-mode rule (only populate when explicitly supported)
    governs. Means-restriction plan fields are included because they describe
    the agreed plan, not the binary presence flags.
    """
    transcript_fields = [
        ("warning_signs", "warning_signs"),
        ("internal_coping", "internal_coping_strategies"),
        ("distractions", "distracting_people_settings"),
        ("help_contacts", "help_people"),
        ("professionals", "professional_contacts"),
        ("means_restriction", "firearms_plan"),
        ("means_restriction", "medications_plan"),
        ("means_restriction", "other_means_plan"),
        ("reasons_for_living", "reasons_for_living"),
    ]

    for section_key, field_key in transcript_fields:
        field = _safety_plan_field(section_key, field_key)
        assert field.ai_hint, f"{section_key}.{field_key} must have an ai_hint"


def test_safety_plan_clinician_and_system_fields_have_no_ai_hint() -> None:
    """Risk level, presence flags, acknowledgments, and signatures stay LLM-silent.

    Per docs/NOTE_TYPES_PRACTICE.md §Safety Plan: ``risk_level``,
    acknowledgments, and signatures are always ``clinician_form`` or
    ``system``; ``firearms_present`` and ``medications_secured`` are binary
    presence flags the clinician sets, not transcript-extractable.
    """
    blank_fields = [
        ("means_restriction", "firearms_present"),
        ("means_restriction", "medications_secured"),
        ("status", "risk_level"),
        ("status", "follow_up_at"),
        ("acknowledgment", "patient_collaboration_acknowledged"),
        ("acknowledgment", "patient_signature_acknowledged"),
        ("signature", "clinician_signature"),
        ("signature", "signed_at"),
    ]

    for section_key, field_key in blank_fields:
        field = _safety_plan_field(section_key, field_key)
        assert field.ai_hint == "", f"{section_key}.{field_key} must have empty ai_hint"


def test_safety_plan_list_fields_use_list_kind() -> None:
    """Each Stanley-Brown step that captures multiple items must be list-kind."""
    assert _safety_plan_field("warning_signs", "warning_signs").kind == "list"
    assert _safety_plan_field("internal_coping", "internal_coping_strategies").kind == "list"
    assert _safety_plan_field("distractions", "distracting_people_settings").kind == "list"
    assert _safety_plan_field("help_contacts", "help_people").kind == "list"
    assert _safety_plan_field("professionals", "professional_contacts").kind == "list"
    assert _safety_plan_field("reasons_for_living", "reasons_for_living").kind == "list"


def _treatment_plan_field(section_key: str, field_key: str):
    section = next(s for s in TREATMENT_PLAN_DEFINITION.sections if s.key == section_key)
    return next(f for f in section.fields if f.key == field_key)


def test_treatment_plan_is_core_patient_tier() -> None:
    """Treatment Plan is patient-context (versioned per patient)."""
    assert TREATMENT_PLAN_DEFINITION.key == "treatment_plan"
    assert TREATMENT_PLAN_DEFINITION.tier == "core"
    assert TREATMENT_PLAN_DEFINITION.context == "patient"


def test_treatment_plan_has_required_sections() -> None:
    """Sections enforce the goal → objective → intervention chain surveyors check."""
    assert TREATMENT_PLAN_DEFINITION.section_keys() == [
        "identification",
        "clinical",
        "goals",
        "objectives",
        "interventions",
        "coordination",
        "discharge",
        "participation",
        "signature",
    ]


def test_treatment_plan_synthesis_fields_have_ai_hints() -> None:
    """Cross-note-synthesis fields (problem_list, goals, strengths_supports) have ai_hint."""
    synthesis_fields = [
        ("clinical", "problem_list"),
        ("clinical", "strengths_supports"),
        ("goals", "goals"),
    ]

    for section_key, field_key in synthesis_fields:
        field = _treatment_plan_field(section_key, field_key)
        assert field.ai_hint, f"{section_key}.{field_key} must have an ai_hint"


def test_treatment_plan_clinician_and_system_fields_have_no_ai_hint() -> None:
    """Objectives, interventions, discharge, signatures, system flags stay LLM-silent.

    The bead's design rule (per ``docs/NOTE_TYPES_PRACTICE.md`` §Treatment Plan)
    is that objectives must be SMART and clinician-judged, interventions must be
    clinician-tied to objectives, and signatures/review_history are system-set.
    Empty ``ai_hint`` enforces that at the schema layer.
    """
    blank_fields = [
        ("identification", "plan_date"),
        ("identification", "plan_type"),
        ("identification", "review_due_date"),
        ("clinical", "dsm5_diagnosis"),
        ("objectives", "objectives"),
        ("interventions", "interventions"),
        ("coordination", "care_coordination"),
        ("discharge", "discharge_termination_criteria"),
        ("participation", "patient_participated_in_plan"),
        ("participation", "patient_signature_present"),
        ("signature", "clinician_signature"),
        ("signature", "signed_at"),
        ("signature", "supervisor_signature"),
        ("signature", "supervisor_signed_at"),
        ("signature", "review_history"),
    ]

    for section_key, field_key in blank_fields:
        field = _treatment_plan_field(section_key, field_key)
        assert field.ai_hint == "", f"{section_key}.{field_key} must have empty ai_hint"


def test_treatment_plan_list_fields_use_list_kind() -> None:
    """Goal/objective/intervention/coordination linkage requires list-kind fields."""
    assert _treatment_plan_field("clinical", "dsm5_diagnosis").kind == "list"
    assert _treatment_plan_field("clinical", "problem_list").kind == "list"
    assert _treatment_plan_field("clinical", "strengths_supports").kind == "list"
    assert _treatment_plan_field("goals", "goals").kind == "list"
    assert _treatment_plan_field("objectives", "objectives").kind == "list"
    assert _treatment_plan_field("interventions", "interventions").kind == "list"
    assert _treatment_plan_field("coordination", "care_coordination").kind == "list"
    assert _treatment_plan_field("signature", "review_history").kind == "list"


def test_patient_context_note_types_route_to_named_bundler_sources() -> None:
    """Drives the real chat-context split (THERAPY-b5913 regression pin).

    Intake, Safety Plan, and Treatment Plan must be ``context="patient"``
    so the OSS chat context bundler's named sources (``MOST_RECENT_INTAKE``,
    ``SAFETY_PLAN_ACTIVE``, ``TREATMENT_PLAN_ACTIVE``) resolve them instead
    of the ``progress_notes_*`` session bucket. A note mis-filed as
    session-context degrades silently — the bundler reports
    ``row_count=0`` / ``reason=no_data``, indistinguishable from "no
    treatment plan on file" — so this asserts the context field directly
    *and* drives one note of each type through ``assemble_context_bundle``
    to prove the routing, not just the label.
    """
    registry = NoteTypeRegistry()
    register_builtin_note_types(registry)

    patient_context_keys = ("intake", "safety_plan", "treatment_plan")
    for key in patient_context_keys:
        assert registry.get(key).context == "patient", (
            f"{key} must be context='patient' or the bundler files it as a "
            "progress note and its named source never resolves"
        )

    patient_id = "patient-b5913"
    user_id = "clinician-b5913"
    now = datetime(2026, 8, 17, tzinfo=UTC)

    notes_repo = InMemoryNotesRepository()
    notes_repo.grant_all_access()
    notes_repo.add(
        Note(
            id="note-intake",
            patient_id=patient_id,
            note_type="intake",
            created_at=now,
            updated_at=now,
            finalized_at=now,
            content={"chief_complaint": "Intake marker text"},
        )
    )
    notes_repo.add(
        Note(
            id="note-safety",
            patient_id=patient_id,
            note_type="safety_plan",
            created_at=now,
            updated_at=now,
            finalized_at=now,
            content={"risk_level": "Safety plan marker text"},
        )
    )
    notes_repo.add(
        Note(
            id="note-treatment",
            patient_id=patient_id,
            note_type="treatment_plan",
            created_at=now,
            updated_at=now,
            finalized_at=now,
            content={"plan_type": "Treatment plan marker text"},
        )
    )

    bundle = assemble_context_bundle(
        notes_repo=notes_repo,
        patient_id=patient_id,
        user_id=user_id,
        selection={
            SOURCE_KEY_MOST_RECENT_INTAKE: True,
            SOURCE_KEY_SAFETY_PLAN_ACTIVE: True,
            SOURCE_KEY_TREATMENT_PLAN_ACTIVE: True,
            SOURCE_KEY_PROGRESS_NOTES_RECENT: True,
        },
    )

    included = {s["source_key"]: s for s in bundle.manifest["sources_included"]}
    assert included[SOURCE_KEY_MOST_RECENT_INTAKE]["row_count"] == 1
    assert included[SOURCE_KEY_SAFETY_PLAN_ACTIVE]["row_count"] == 1
    assert included[SOURCE_KEY_TREATMENT_PLAN_ACTIVE]["row_count"] == 1
    assert included[SOURCE_KEY_PROGRESS_NOTES_RECENT]["row_count"] == 0

    assert "MOST RECENT INTAKE" in bundle.text
    assert "ACTIVE SAFETY PLAN" in bundle.text
    assert "ACTIVE TREATMENT PLAN" in bundle.text
    assert "RECENT PROGRESS NOTES" not in bundle.text

    assert "Intake marker text" in bundle.text
    assert "Safety plan marker text" in bundle.text
    assert "Treatment plan marker text" in bundle.text
