# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Related history sections drafted in bounded structured calls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .hpi_section_call import uncovered_text

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..notes.section_calls import SectionField

CLINICAL_HISTORY_SCHEMA_TITLE = "ClinicalHistorySections"
BACKGROUND_SCHEMA_TITLE = "BackgroundHistorySections"

SYSTEM_PROMPT = (
    "You draft related history sections of a clinician's note from one visit's transcript. "
    "Write only what the visit says, without inference or attribution phrasing. Return only "
    "the JSON object requested."
)

_UNCOVERED = frozenset({"", "not discussed", "not asked", "not stated"})


def response_schema(fields: Sequence[SectionField], *, title: str) -> dict[str, Any]:
    """A bounded schema whose sections and fields are all required."""
    sections: dict[str, dict[str, Any]] = {}
    for routed in fields:
        section = sections.setdefault(
            routed.section,
            {
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
        )
        field_schema: dict[str, Any]
        if routed.field.kind == "list":
            field_schema = {"type": "array", "items": {"type": "string"}}
        else:
            field_schema = {"type": "string"}
        section["properties"][routed.field.key] = field_schema
        section["required"].append(routed.field.key)
    return {
        "type": "object",
        "title": title,
        "properties": sections,
        "required": list(sections),
        "additionalProperties": False,
    }


def build_prompt(
    fields: Sequence[SectionField],
    person: str,
    transcript_content: str,
    current_note: str | None = None,
) -> str:
    lines = [
        f"Draft these related history fields about the {person}.",
        "Use concise, third-person clinical prose. Preserve pertinent positives and negatives.",
        'Use each field hint\'s uncovered wording (such as "Not asked." or "Not stated.") when '
        "the visit did not cover it.",
        "Do not write who said, asked, noted, or documented a finding.",
        "",
        "Fields:",
    ]
    for routed in fields:
        lines.append(
            f"- {routed.path} ({routed.field.label}): {routed.field.ai_hint or routed.field.label}"
        )
    if current_note:
        lines.extend(["", current_note])
    lines.extend(["", "Transcript:", transcript_content])
    return "\n".join(lines)


def compose(reply: Mapping[str, Any], fields: Sequence[SectionField]) -> dict[str, dict[str, Any]]:
    content: dict[str, dict[str, Any]] = {}
    for routed in fields:
        raw_section = reply.get(routed.section)
        raw = raw_section.get(routed.field.key) if isinstance(raw_section, dict) else None
        if routed.field.kind == "list":
            value: Any = (
                [str(item).strip() for item in raw if item] if isinstance(raw, list) else []
            )
        else:
            text = str(raw).strip() if raw else ""
            value = (
                uncovered_text(routed) if text.rstrip(".").strip().lower() in _UNCOVERED else text
            )
        content.setdefault(routed.section, {})[routed.field.key] = value
    return content


def draft_sections(
    complete: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    fields: Sequence[SectionField],
    person: str,
    transcript_content: str,
    current_note: str | None = None,
    *,
    title: str,
) -> dict[str, dict[str, Any]]:
    if not fields:
        return {}
    reply = complete(
        SYSTEM_PROMPT,
        build_prompt(fields, person, transcript_content, current_note),
        response_schema(fields, title=title),
    )
    return compose(reply, fields)
