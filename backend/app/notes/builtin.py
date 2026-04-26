# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Built-in OSS note-type definitions: SOAP + Narrative.

SOAP mirrors :class:`app.models.soap_note.SOAPNote` exactly so the upcoming
schema-driven generation path is behavior-preserving. Narrative is a single
free-form section used for non-structured session notes.
"""

from __future__ import annotations

from .registry import (
    NoteFieldDef,
    NoteSectionDef,
    NoteTypeDefinition,
    NoteTypeRegistry,
)

SOAP_DEFINITION = NoteTypeDefinition(
    key="soap",
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


def register_builtin_note_types(registry: NoteTypeRegistry) -> None:
    """Register OSS note types (SOAP + Narrative) on ``registry``.

    Idempotent: if called twice on the same registry, re-registers with
    ``replace=True`` so startup ordering and tests stay simple.
    """
    for definition in (SOAP_DEFINITION, NARRATIVE_DEFINITION):
        registry.register(definition, replace=True)
