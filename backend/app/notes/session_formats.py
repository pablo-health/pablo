# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""DAP, BIRP and GIRP: alternate structures for a session note.

Each is a session-context format with the same generation path as SOAP;
they differ only in how the note is organized. ``ai_hint`` text is the
prompt the generation service uses for that field, so wording matters.
"""

from __future__ import annotations

from .registry import NoteFieldDef, NoteSectionDef, NoteTypeDefinition

DAP_DEFINITION = NoteTypeDefinition(
    key="dap",
    label="DAP",
    description=(
        "Data / Assessment / Plan — the most common SOAP alternative for "
        "individual therapy. Collapses the Subjective/Objective split into "
        "a single Data section."
    ),
    tier="core",
    context="session",
    sections=(
        NoteSectionDef(
            key="data",
            label="Data",
            fields=(
                NoteFieldDef(
                    key="presenting_concern",
                    label="Presenting Concern",
                    kind="text",
                    ai_hint="The primary issue the client raised in this session.",
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
                    key="session_content",
                    label="Session Content",
                    kind="text",
                    ai_hint="A concise narrative of what the client discussed in session.",
                ),
                NoteFieldDef(
                    key="observations",
                    label="Observations",
                    kind="text",
                    ai_hint="Clinician-observed presentation, behavior, and speech.",
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


BIRP_DEFINITION = NoteTypeDefinition(
    key="birp",
    label="BIRP",
    description=(
        "Behavior / Intervention / Response / Plan — standard for group "
        "therapy, residential treatment, and substance-use disorder "
        "programs. Emphasizes the therapeutic process over diagnosis."
    ),
    tier="core",
    context="session",
    sections=(
        NoteSectionDef(
            key="behavior",
            label="Behavior",
            fields=(
                NoteFieldDef(
                    key="presenting_behavior",
                    label="Presenting Behavior",
                    kind="text",
                    ai_hint="What the client did and said during the session.",
                ),
                NoteFieldDef(
                    key="mood_affect",
                    label="Mood/Affect",
                    kind="text",
                    ai_hint="Client's self-reported mood and observed affective tone.",
                ),
                NoteFieldDef(
                    key="reported_symptoms",
                    label="Reported Symptoms",
                    kind="list",
                    ai_hint="Discrete symptoms the client reported in this session.",
                ),
            ),
        ),
        NoteSectionDef(
            key="intervention",
            label="Intervention",
            fields=(
                NoteFieldDef(
                    key="interventions_used",
                    label="Interventions Used",
                    kind="list",
                    ai_hint="Therapeutic techniques the clinician applied during the session.",
                ),
                NoteFieldDef(
                    key="rationale",
                    label="Rationale",
                    kind="text",
                    ai_hint="Why these interventions were chosen for this client at this time.",
                ),
            ),
        ),
        NoteSectionDef(
            key="response",
            label="Response",
            fields=(
                NoteFieldDef(
                    key="client_response",
                    label="Client Response",
                    kind="text",
                    ai_hint="How the client responded to the interventions used.",
                ),
                NoteFieldDef(
                    key="engagement_level",
                    label="Engagement Level",
                    kind="text",
                    ai_hint=(
                        "Observed engagement during the session "
                        "(resistant, ambivalent, engaged, etc.)."
                    ),
                ),
                NoteFieldDef(
                    key="progress_observed",
                    label="Progress Observed",
                    kind="text",
                    ai_hint=(
                        "Within-session shifts the clinician noted in the client's presentation."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="plan",
            label="Plan",
            fields=(
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


GIRP_DEFINITION = NoteTypeDefinition(
    key="girp",
    label="GIRP",
    description=(
        "Goal / Intervention / Response / Plan — outcome-driven format "
        "common where insurers require goal and outcome tracking aligned "
        "to a treatment plan."
    ),
    tier="core",
    context="session",
    sections=(
        NoteSectionDef(
            key="goal",
            label="Goal",
            fields=(
                NoteFieldDef(
                    key="session_goal",
                    label="Session Goal",
                    kind="text",
                    ai_hint="The specific goal or focus area being worked on this session.",
                ),
                NoteFieldDef(
                    key="treatment_plan_objective",
                    label="Treatment Plan Objective",
                    kind="text",
                    ai_hint=(
                        "The overarching objective from the treatment plan this session targets."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="intervention",
            label="Intervention",
            fields=(
                NoteFieldDef(
                    key="interventions_used",
                    label="Interventions Used",
                    kind="list",
                    ai_hint="Therapeutic techniques the clinician applied this session.",
                ),
                NoteFieldDef(
                    key="rationale",
                    label="Rationale",
                    kind="text",
                    ai_hint="Why these interventions match the stated goal.",
                ),
            ),
        ),
        NoteSectionDef(
            key="response",
            label="Response",
            fields=(
                NoteFieldDef(
                    key="client_response",
                    label="Client Response",
                    kind="text",
                    ai_hint="How the client responded to the interventions used.",
                ),
                NoteFieldDef(
                    key="progress_toward_goal",
                    label="Progress Toward Goal",
                    kind="text",
                    ai_hint="Observed movement toward the session goal.",
                ),
                NoteFieldDef(
                    key="measurable_outcome",
                    label="Measurable Outcome",
                    kind="text",
                    ai_hint="Observable change during or after the session.",
                ),
            ),
        ),
        NoteSectionDef(
            key="plan",
            label="Plan",
            fields=(
                NoteFieldDef(
                    key="homework_assignments",
                    label="Homework Assignments",
                    kind="list",
                    ai_hint="Tasks aligned to the session goal, assigned between sessions.",
                ),
                NoteFieldDef(
                    key="next_steps",
                    label="Next Steps",
                    kind="list",
                    ai_hint="Planned focus for the next session.",
                ),
                NoteFieldDef(
                    key="goal_status",
                    label="Goal Status",
                    kind="text",
                    ai_hint=(
                        "Status of the session goal: achieved, partial, no progress, or modified."
                    ),
                ),
            ),
        ),
    ),
)
