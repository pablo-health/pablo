# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Meeting Summary: professional minutes for a recorded business meeting.

The one non-clinical session-context format, for recordings that are
business meetings rather than therapy sessions, such as a call with a
vendor or referral partner, run through the same record, transcribe
and generate pipeline as a clinical note. It carries its own system
prompt and prompt builder because the default framing assumes a
therapy-session transcript.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import NoteFieldDef, NoteSectionDef, NoteTypeDefinition

if TYPE_CHECKING:
    from datetime import datetime

    from ..models import Patient, Transcript


MEETING_SUMMARY_SYSTEM_PROMPT = (
    "You are drafting professional meeting minutes from the transcript of a "
    "business meeting — for example a call with a prospective customer, "
    "vendor, referral partner, or colleague. No one in it is a patient or "
    "client.\n\n"
    "Use plain professional language: no clinical terminology, no "
    "assessment of anyone's mental state, no diagnosis or treatment "
    "language.\n\n"
    "Attribute decisions and commitments to the people who made them, using "
    "the names or roles the transcript gives.\n\n"
    "Never invent attendees, decisions, dates, or commitments the "
    "transcript does not support; leave a field empty when the transcript "
    "says nothing about it.\n\n"
    "Respond only with the requested structured fields."
)


def build_meeting_summary_prompt(
    definition: NoteTypeDefinition,
    transcript: Transcript,
    _patient: Patient,
    session_date: datetime,
) -> str:
    """Compose the Meeting Summary generation user prompt.

    The business-meeting framing lives in ``MEETING_SUMMARY_SYSTEM_PROMPT``.
    This builder mirrors the registry's auto-synthesized field enumeration
    plus the meeting date and transcript.
    """
    lines: list[str] = [
        "Draft meeting minutes for the transcript below. Leave a field "
        "empty when the transcript says nothing about it.",
        "",
        "Fields:",
    ]
    for section in definition.sections:
        lines.append(f"- Section '{section.key}' ({section.label}):")
        for f in section.fields:
            hint = f.ai_hint or f.label
            kind_label = {
                "text": "free-form string",
                "list": "list of short strings",
                "structured": "nested object",
            }[f.kind]
            lines.append(f"    * {f.key} ({kind_label}) — {hint}")
    lines.extend(
        [
            "",
            f"Meeting date: {session_date.isoformat().split('T', 1)[0]}",
            "",
            "Transcript:",
            transcript.content,
        ]
    )
    return "\n".join(lines)


MEETING_SUMMARY_DEFINITION = NoteTypeDefinition(
    key="meeting_summary",
    prompt_builder=build_meeting_summary_prompt,
    system_prompt=MEETING_SUMMARY_SYSTEM_PROMPT,
    label="Meeting Summary",
    description=(
        "Professional minutes for a recorded business meeting — attendees, "
        "discussion, decisions, and action items. For calls that are not "
        "clinical sessions, e.g. vendors, referral partners, or consultants."
    ),
    tier="core",
    context="session",
    sections=(
        NoteSectionDef(
            key="overview",
            label="Overview",
            fields=(
                NoteFieldDef(
                    key="attendees",
                    label="Attendees",
                    kind="list",
                    ai_hint=(
                        "People present in the meeting, with role or affiliation "
                        "when stated in the transcript. Never invent names."
                    ),
                ),
                NoteFieldDef(
                    key="purpose",
                    label="Purpose",
                    kind="text",
                    ai_hint=(
                        "Why the meeting happened — the stated objective or agenda, "
                        "in one or two sentences."
                    ),
                ),
                NoteFieldDef(
                    key="summary",
                    label="Summary",
                    kind="text",
                    ai_hint=(
                        "A concise narrative of the discussion: the main topics, "
                        "each party's position, and how the conversation concluded."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="outcomes",
            label="Outcomes",
            fields=(
                NoteFieldDef(
                    key="key_points",
                    label="Key Points",
                    kind="list",
                    ai_hint="The most important takeaways from the discussion.",
                ),
                NoteFieldDef(
                    key="decisions",
                    label="Decisions",
                    kind="list",
                    ai_hint=(
                        "Decisions actually made in the meeting, each with who "
                        "made or agreed to it. Exclude options merely discussed."
                    ),
                ),
                NoteFieldDef(
                    key="action_items",
                    label="Action Items",
                    kind="list",
                    ai_hint=(
                        "Concrete commitments, each with its owner and due date "
                        "when the transcript states them."
                    ),
                ),
                NoteFieldDef(
                    key="open_questions",
                    label="Open Questions",
                    kind="list",
                    ai_hint=(
                        "Questions raised but not resolved, or topics explicitly "
                        "deferred to a later conversation."
                    ),
                ),
            ),
        ),
        NoteSectionDef(
            key="follow_up",
            label="Follow-up",
            fields=(
                NoteFieldDef(
                    key="next_meeting",
                    label="Next Meeting",
                    kind="text",
                    ai_hint=(
                        "Agreed date, time, or cadence for the next touchpoint, "
                        "if one was discussed."
                    ),
                ),
            ),
        ),
    ),
)
