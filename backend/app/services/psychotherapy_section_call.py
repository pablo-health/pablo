# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The psychotherapy call: a visit's psychotherapy block drafted on its own.

A small structured call beside the main draft. It sees the transcript with each
line numbered and the fields of the psychotherapy section
(:mod:`app.notes.section_calls`) with their hints, and returns what the therapy
worked on, how, the client's response, the goal, progress and cadence. The
response alone carries the client's words: it comes back as what the client
said or did in the note's voice plus each quotation with the numbered lines it
is copied from, and is composed here. A quotation is kept only when every line
it cites contains its words, and is then copied from the line
(:func:`.risk_section_call.verified_words`).

The time of the therapy is not this call's: the main draft returns what the
clinician dictated and the turn labels count the rest
(:mod:`app.services.therapy_labels`). Whether this call runs at all is decided
in code first: when the labelled turns hold no therapy and no time was
dictated, the block is left empty and no model is asked
(:func:`no_therapy`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..chart_proposals.drafting import segment_texts
from ..notes.visit_times import THERAPY
from .hpi_section_call import compose_chief_complaint, uncovered_text
from .source_attribution_service import format_transcript_with_segment_ids

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..notes.section_calls import SectionField
    from ..notes.visit_times import TurnLabel

SCHEMA_TITLE = "PsychotherapySections"

SYSTEM_PROMPT = (
    "You draft the psychotherapy portion of a clinician's note from one visit's "
    "transcript: the therapy that took place, as the transcript shows it. You write only "
    "what the visit says: you never infer, judge or add. Return only the JSON object asked for."
)

_INSTRUCTIONS = """\
Draft each field below from the psychotherapy in this visit, in the note's own voice: concise, \
third person, past tense, "the {term}" or they, never he or she. No field says who said, asked, \
noted or stated anything ("the clinician stated" included).
- Name a technique only when the clinician named it or did its steps. Where the clinician \
listened, reflected or asked open questions and named nothing, say that in plain words.
- Numbers only as the {term} gave them, in their words ("twice" stays "twice"); a rating or a \
count only as the {term} said it, never one of your own.
- A field with quotes: text says briefly what the {term} said or did; quotes are at most two of \
the {term}'s own key words, each copied exactly from one {term} line, segment_ids that line's n \
in [Sn]. No other field has quotation marks.
- Progress only where a rating moved or an earlier assignment was done, never judged \
("meaningful", "improved").
- The goal only as the clinician or the {term} stated it. An assignment, or how often therapy \
continues, is not a goal.
- Cadence only as the clinician said how often therapy continues ("weekly"); when the next \
visit is booked is not a cadence and reads "Not stated.".
- Never a billing code, a clock time or a number of minutes: the note records those elsewhere.
- Medications, symptom review, questionnaire scores (a PHQ-9, a GAD-7) and risk questions belong \
to other sections; leave them out.
- If no psychotherapy took place, or the visit code entered is a psychiatric diagnostic \
evaluation, leave every field empty. Otherwise "Not stated." for a field the therapy did \
not cover."""

_EVIDENCE = {"type": "array", "items": {"type": "integer"}}
_QUOTED_FIELD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"words": {"type": "string"}, "segment_ids": _EVIDENCE},
                "required": ["words", "segment_ids"],
            },
        },
    },
    "required": ["text", "quotes"],
}


def no_therapy(
    labels: Mapping[float, TurnLabel] | None, dictated_time: bool, current_block: bool
) -> bool:
    """Whether the block is empty without asking a model.

    True only when the turns were labelled, none of them is therapy, no time
    was dictated, and a redraft's note has nothing in the block. Labels that
    could not be made (no timed turns, a failed call) decide nothing: the
    call then drafts, and leaves the block empty itself when there was no
    therapy.
    """
    if not labels or dictated_time or current_block:
        return False
    return THERAPY not in labels.values()


def empty_block(fields: Sequence[SectionField]) -> dict[str, dict[str, Any]]:
    """Every field of the block empty, as a visit with no therapy has it."""
    content: dict[str, dict[str, Any]] = {}
    for f in fields:
        content.setdefault(f.section, {})[f.field.key] = [] if f.field.kind == "list" else ""
    return content


def _field_schema(f: SectionField) -> dict[str, Any]:
    if f.quotes:
        return _QUOTED_FIELD
    if f.field.kind == "list":
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def response_schema(fields: Sequence[SectionField]) -> dict[str, Any]:
    """The reply's shape: the block's fields, the response's quotes apart."""
    sections: dict[str, dict[str, Any]] = {}
    for f in fields:
        section = sections.setdefault(
            f.section, {"type": "object", "properties": {}, "required": []}
        )
        section["properties"][f.field.key] = _field_schema(f)
        section["required"].append(f.field.key)
    return {
        "type": "object",
        "title": SCHEMA_TITLE,
        "properties": sections,
        "required": list(sections),
    }


def build_prompt(
    fields: Sequence[SectionField],
    person: str,
    indexed_transcript: str,
    current_note: str | None = None,
) -> str:
    lines = [_INSTRUCTIONS.format(term=person), "", "Fields:"]
    for f in fields:
        shape = "; text and quotes" if f.quotes else ""
        lines.append(f"- {f.path} ({f.field.label}{shape}): {f.field.ai_hint or f.field.label}")
    if current_note:
        lines.extend(["", current_note])
    lines.extend(["", "Transcript (each line numbered [Sn]):", indexed_transcript])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

_UNCOVERED = frozenset({"", "not stated", "not discussed", "none"})


def _is_uncovered(text: str) -> bool:
    return text.strip().rstrip(".").strip().lower() in _UNCOVERED


def _raw(reply: Mapping[str, Any], f: SectionField) -> Any:
    section = reply.get(f.section)
    return section.get(f.field.key) if isinstance(section, dict) else None


def _text_of(raw: Any) -> str:
    if isinstance(raw, dict):
        raw = raw.get("text")
    if isinstance(raw, list):
        return " ".join(str(item) for item in raw if item)
    return str(raw).strip() if raw else ""


def compose(
    reply: Mapping[str, Any],
    fields: Sequence[SectionField],
    segments: Mapping[int, str],
    person: str,
) -> dict[str, dict[str, Any]]:
    """Each field's content by section and key, from the reply.

    A reply that wrote nothing in any field is a visit with no therapy, and the
    block stays empty; otherwise a field the therapy did not cover reads as its
    hint says.
    """
    quoted = any(
        isinstance(raw, dict) and raw.get("quotes")
        for raw in (_raw(reply, f) for f in fields if f.quotes)
    )
    if not quoted and all(_is_uncovered(_text_of(_raw(reply, f))) for f in fields):
        return empty_block(fields)
    content: dict[str, dict[str, Any]] = {}
    for f in fields:
        raw = _raw(reply, f)
        value: Any
        if f.quotes:
            parts = raw if isinstance(raw, dict) else {"text": raw, "quotes": []}
            value = compose_chief_complaint(
                str(parts.get("text") or ""),
                parts.get("quotes"),
                segments,
                person,
                uncovered_text(f),
            )
        elif f.field.kind == "list":
            value = [str(item).strip() for item in raw if item] if isinstance(raw, list) else []
        else:
            text = _text_of(raw)
            value = uncovered_text(f) if _is_uncovered(text) else text
        content.setdefault(f.section, {})[f.field.key] = value
    return content


def draft_sections(
    complete: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    fields: Sequence[SectionField],
    person: str,
    transcript_content: str,
    current_note: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Draft ``fields`` from the visit. Raises what ``complete`` raises, as the main draft does."""
    if not fields:
        return {}
    indexed = format_transcript_with_segment_ids(transcript_content)
    reply = complete(
        SYSTEM_PROMPT,
        build_prompt(fields, person, indexed, current_note),
        response_schema(fields),
    )
    return compose(reply, fields, segment_texts(indexed) if indexed else {}, person)
