# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Built-in OSS note-type definitions.

SOAP mirrors :class:`app.models.soap_note.SOAPNote` exactly so the upcoming
schema-driven generation path is behavior-preserving. Narrative is a single
free-form section used for non-structured session notes. Intake, Treatment
Plan, Safety Plan, and Medications are patient-context formats that follow
the client rather than a single session.
"""

from __future__ import annotations

from .prompts.soap import build_soap_prompt
from .registry import (
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeDefinition,
    NoteTypeRegistry,
)

SOAP_DEFINITION = NoteTypeDefinition(
    key="soap",
    prompt_builder=build_soap_prompt,
    label="SOAP",
    description=(
        "Subjective / Objective / Assessment / Plan — the default clinical "
        "format used for individual therapy sessions."
    ),
    tier="core",
    sections=(
        NoteSectionDef(
            key="subjective",
            label="Subjective",
            fields=(
                NoteFieldDef(
                    key="chief_complaint",
                    label="Chief Complaint",
                    kind="text",
                    ai_hint="The primary reason the client stated for attending this session.",
                ),
                NoteFieldDef(
                    key="mood_affect",
                    label="Mood/Affect",
                    kind="text",
                    ai_hint="Client's self-reported mood and observed affective tone.",
                ),
                NoteFieldDef(
                    key="symptoms",
                    label="Symptoms",
                    kind="list",
                    ai_hint="Discrete symptoms the client reported in this session.",
                ),
                NoteFieldDef(
                    key="client_narrative",
                    label="Client Narrative",
                    kind="text",
                    ai_hint="A concise narrative of what the client discussed.",
                ),
            ),
        ),
        NoteSectionDef(
            key="objective",
            label="Objective",
            fields=(
                NoteFieldDef(
                    key="appearance",
                    label="Appearance",
                    kind="text",
                    ai_hint="Observed physical presentation of the client.",
                ),
                NoteFieldDef(
                    key="behavior",
                    label="Behavior",
                    kind="text",
                    ai_hint="Observed behavioral patterns during the session.",
                ),
                NoteFieldDef(
                    key="speech",
                    label="Speech",
                    kind="text",
                    ai_hint="Rate, rhythm, volume, and coherence of speech.",
                ),
                NoteFieldDef(
                    key="thought_process",
                    label="Thought Process",
                    kind="text",
                    ai_hint="Organization and logic of the client's thinking.",
                ),
                NoteFieldDef(
                    key="affect_observed",
                    label="Affect Observed",
                    kind="text",
                    ai_hint="Clinician-observed affective presentation.",
                ),
            ),
        ),
        NoteSectionDef(
            key="assessment",
            label="Assessment",
            fields=(
                NoteFieldDef(
                    key="clinical_impression",
                    label="Clinical Impression",
                    kind="text",
                    ai_hint="Clinician's overall impression of the client's current state.",
                ),
                NoteFieldDef(
                    key="progress",
                    label="Progress",
                    kind="text",
                    ai_hint="Movement relative to treatment goals since the last session.",
                ),
                NoteFieldDef(
                    key="risk_assessment",
                    label="Risk Assessment",
                    kind="text",
                    ai_hint="Any observed or reported risk to self or others.",
                ),
                NoteFieldDef(
                    key="functioning_level",
                    label="Functioning Level",
                    kind="text",
                    ai_hint="Client's current functional status.",
                ),
            ),
        ),
        NoteSectionDef(
            key="plan",
            label="Plan",
            fields=(
                NoteFieldDef(
                    key="interventions_used",
                    label="Interventions Used",
                    kind="list",
                    ai_hint="Therapeutic interventions applied during this session.",
                ),
                NoteFieldDef(
                    key="homework_assignments",
                    label="Homework Assignments",
                    kind="list",
                    ai_hint="Tasks or practices assigned to the client between sessions.",
                ),
                NoteFieldDef(
                    key="next_steps",
                    label="Next Steps",
                    kind="list",
                    ai_hint="Planned clinical focus for upcoming sessions.",
                ),
                NoteFieldDef(
                    key="next_session",
                    label="Next Session",
                    kind="text",
                    ai_hint="Scheduled date/time or cadence for the next appointment.",
                ),
            ),
        ),
    ),
)


NARRATIVE_DEFINITION = NoteTypeDefinition(
    key="narrative",
    label="Narrative",
    description=(
        "A single free-form narrative note for sessions that do not fit a structured format."
    ),
    tier="core",
    sections=(
        NoteSectionDef(
            key="note",
            label="Note",
            fields=(
                NoteFieldDef(
                    key="body",
                    label="Note",
                    kind="text",
                    ai_hint=("A clinically-appropriate narrative summary of the session."),
                ),
            ),
        ),
    ),
)


INTAKE_DEFINITION = NoteTypeDefinition(
    key="intake",
    label="Intake",
    description=(
        "Initial biopsychosocial assessment completed at the start of care, "
        "covering presenting concerns, history, and initial formulation."
    ),
    tier="core",
    context="patient",
    sections=(
        NoteSectionDef(
            key="presenting_concerns",
            label="Presenting Concerns",
            fields=(
                NoteFieldDef(
                    key="chief_complaint",
                    label="Chief Complaint",
                    kind="text",
                    ai_hint="The primary reason the client is seeking care, in their own words.",
                ),
                NoteFieldDef(
                    key="history_of_present_illness",
                    label="History of Present Illness",
                    kind="text",
                    ai_hint="Onset, duration, and course of the presenting concern.",
                ),
            ),
        ),
        NoteSectionDef(
            key="history",
            label="History",
            fields=(
                NoteFieldDef(
                    key="psychiatric_history",
                    label="Psychiatric History",
                    kind="text",
                    ai_hint="Prior mental health diagnoses, treatment, and hospitalizations.",
                ),
                NoteFieldDef(
                    key="medical_history",
                    label="Medical History",
                    kind="text",
                    ai_hint="Relevant medical conditions and current treatment.",
                ),
                NoteFieldDef(
                    key="family_history",
                    label="Family History",
                    kind="text",
                    ai_hint="Family history of mental illness, substance use, or medical issues.",
                ),
                NoteFieldDef(
                    key="social_history",
                    label="Social History",
                    kind="text",
                    ai_hint="Living situation, relationships, education, employment, and support.",
                ),
            ),
        ),
        NoteSectionDef(
            key="substance_use",
            label="Substance Use",
            fields=(
                NoteFieldDef(
                    key="substance_use_history",
                    label="Substance Use History",
                    kind="text",
                    ai_hint="Current and past use of alcohol, tobacco, and other substances.",
                ),
            ),
        ),
        NoteSectionDef(
            key="risk",
            label="Risk",
            fields=(
                NoteFieldDef(
                    key="risk_assessment",
                    label="Risk Assessment",
                    kind="text",
                    ai_hint="Suicidality, self-harm, harm to others, and any safety concerns.",
                ),
            ),
        ),
        NoteSectionDef(
            key="formulation",
            label="Formulation",
            fields=(
                NoteFieldDef(
                    key="initial_formulation",
                    label="Initial Formulation",
                    kind="text",
                    ai_hint="Clinician's initial diagnostic impression and case conceptualization.",
                ),
            ),
        ),
    ),
)


TREATMENT_PLAN_DEFINITION = NoteTypeDefinition(
    key="treatment_plan",
    label="Treatment Plan",
    description=(
        "The client's active problems, goals, and objectives, with the "
        "interventions in use and the next scheduled review."
    ),
    tier="core",
    context="patient",
    sections=(
        NoteSectionDef(
            key="problems",
            label="Problems",
            fields=(
                NoteFieldDef(
                    key="problem_list",
                    label="Problem List",
                    kind="list",
                    ai_hint="Clinical problems being addressed in treatment.",
                ),
            ),
        ),
        NoteSectionDef(
            key="goals",
            label="Goals",
            fields=(
                NoteFieldDef(
                    key="goal_list",
                    label="Goals",
                    kind="list",
                    ai_hint="Long-term treatment goals tied to the problem list.",
                ),
                NoteFieldDef(
                    key="objective_list",
                    label="Objectives",
                    kind="list",
                    ai_hint="Short-term, measurable objectives supporting each goal.",
                ),
            ),
        ),
        NoteSectionDef(
            key="interventions",
            label="Interventions",
            fields=(
                NoteFieldDef(
                    key="intervention_list",
                    label="Interventions",
                    kind="list",
                    ai_hint="Therapeutic interventions and modalities used to pursue the goals.",
                ),
            ),
        ),
        NoteSectionDef(
            key="review",
            label="Review",
            fields=(
                NoteFieldDef(
                    key="review_date",
                    label="Review Date",
                    kind="text",
                    ai_hint="Date this treatment plan is next scheduled for review.",
                ),
            ),
        ),
    ),
)


SAFETY_PLAN_DEFINITION = NoteTypeDefinition(
    key="safety_plan",
    label="Safety Plan",
    description=(
        "A Stanley-Brown style safety plan: warning signs, coping strategies, "
        "and the people and professionals a client can turn to in crisis."
    ),
    tier="core",
    context="patient",
    sections=(
        NoteSectionDef(
            key="warning_signs",
            label="Warning Signs",
            fields=(
                NoteFieldDef(
                    key="warning_signs",
                    label="Warning Signs",
                    kind="list",
                    ai_hint="Thoughts or situations that signal a crisis may be developing.",
                ),
            ),
        ),
        NoteSectionDef(
            key="internal_coping",
            label="Internal Coping Strategies",
            fields=(
                NoteFieldDef(
                    key="coping_strategies",
                    label="Internal Coping Strategies",
                    kind="list",
                    ai_hint="Things the client can do alone to take their mind off problems.",
                ),
            ),
        ),
        NoteSectionDef(
            key="social_distraction",
            label="People and Social Settings That Provide Distraction",
            fields=(
                NoteFieldDef(
                    key="distractions",
                    label="People and Social Settings",
                    kind="list",
                    ai_hint="People and social settings that provide distraction from a crisis.",
                ),
            ),
        ),
        NoteSectionDef(
            key="social_support",
            label="People to Ask for Help",
            fields=(
                NoteFieldDef(
                    key="support_contacts",
                    label="People to Ask for Help",
                    kind="list",
                    ai_hint="Family or friends the client can ask for help during a crisis.",
                ),
            ),
        ),
        NoteSectionDef(
            key="professional_contacts",
            label="Professionals and Agencies to Contact",
            fields=(
                NoteFieldDef(
                    key="professional_contacts",
                    label="Professionals and Agencies",
                    kind="list",
                    ai_hint="Clinicians, crisis lines, and agencies the client can contact.",
                ),
            ),
        ),
        NoteSectionDef(
            key="environment_safety",
            label="Making the Environment Safe",
            fields=(
                NoteFieldDef(
                    key="environment_safety",
                    label="Making the Environment Safe",
                    kind="text",
                    ai_hint="Steps to limit access to lethal means during a crisis.",
                ),
            ),
        ),
    ),
)


MEDICATIONS_DEFINITION = NoteTypeDefinition(
    key="medications",
    label="Medications",
    description="The client's current and past medications.",
    tier="core",
    context="patient",
    sections=(
        NoteSectionDef(
            key="medication_list",
            label="Medications",
            fields=(
                NoteFieldDef(
                    key="drug",
                    label="Drug",
                    kind="list",
                    ai_hint="Medication names, current and past.",
                ),
                NoteFieldDef(
                    key="dose",
                    label="Dose",
                    kind="list",
                    ai_hint="Dosage for each medication.",
                ),
                NoteFieldDef(
                    key="route",
                    label="Route",
                    kind="list",
                    ai_hint="Route of administration for each medication.",
                ),
                NoteFieldDef(
                    key="frequency",
                    label="Frequency",
                    kind="list",
                    ai_hint="How often each medication is taken.",
                ),
                NoteFieldDef(
                    key="prescriber",
                    label="Prescriber",
                    kind="list",
                    ai_hint="Prescribing clinician for each medication.",
                ),
                NoteFieldDef(
                    key="start_stop_dates",
                    label="Start/Stop Dates",
                    kind="list",
                    ai_hint="Start date, and stop date if discontinued, for each medication.",
                ),
                NoteFieldDef(
                    key="notes",
                    label="Notes",
                    kind="text",
                    ai_hint="Additional notes on efficacy, side effects, or adherence.",
                ),
            ),
        ),
    ),
)


def register_builtin_note_types(registry: NoteTypeRegistry) -> None:
    """Register OSS note types on ``registry``.

    Idempotent: if called twice on the same registry, re-registers with
    ``replace=True`` so startup ordering and tests stay simple.
    """
    for definition in (
        SOAP_DEFINITION,
        NARRATIVE_DEFINITION,
        INTAKE_DEFINITION,
        TREATMENT_PLAN_DEFINITION,
        SAFETY_PLAN_DEFINITION,
        MEDICATIONS_DEFINITION,
    ):
        registry.register(definition, replace=True)
