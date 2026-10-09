# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The history of present illness call: that section drafted on its own.

A small structured call beside the main draft, the extraction and the risk
call. It sees the transcript with each line numbered and the fields of the
sections routed to it (:mod:`app.notes.section_calls`) with their hints, and
returns each field's text in the note's own voice. The chief complaint alone
carries the client's words: it comes back as the reason for the visit plus
each quotation with the numbered lines it is copied from, and is composed
here. A quotation is kept only when every line it cites contains its words,
and is then copied from the line (:func:`.risk_section_call.verified_words`);
one that fails the check is dropped and the reason stands alone.

A field the visit never touched reads as its hint says ("Not discussed." for a
follow-up's symptom domain, "Not asked." for an evaluation's review of
systems), whatever the reply wrote for it, empty included.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..chart_proposals.drafting import segment_texts
from .risk_section_call import verified_words
from .source_attribution_service import format_transcript_with_segment_ids

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..notes.section_calls import SectionField

SCHEMA_TITLE = "HistoryOfPresentIllnessSections"

SYSTEM_PROMPT = (
    "You draft the history of present illness of a clinician's note from one visit's "
    "transcript: what was reported, domain by domain. You write only what the visit says: "
    "you never infer, judge or add. Return only the JSON object asked for."
)

NOT_DISCUSSED = "Not discussed."
NOT_STATED = "Not stated."

_INSTRUCTIONS = """\
Draft each field below from this visit: the history it took, domain by domain.
- Each domain: this visit's pertinent positives and negatives, in the note's own voice: concise, \
third person, past tense, "the {term}" or they, never he or she. A denial is recorded as a denial.
- What the clinician observed or said, during the visit or after the {term}'s last line, is \
written as a finding: declarative, never saying who said, asked, noted or dictated it.
- No quotation marks, except that a field with quotes carries the {term}'s words: text is the \
reason for the visit in the note's voice, and each quote is the {term}'s own words about why they \
came, copied exactly from one {term} line, segment_ids that line's n in [Sn].
- A domain takes what was said about it anywhere in the visit, in passing or under another \
topic included. "Not discussed." (or the words its hint gives) only for a domain nothing in the \
visit touched; never a denial nobody said.
- A score, level or diagnosis only as the clinician stated it."""

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


def _field_schema(f: SectionField) -> dict[str, Any]:
    if f.quotes:
        return _QUOTED_FIELD
    if f.field.kind == "list":
        return {"type": "array", "items": {"type": "string"}}
    return {"type": "string"}


def response_schema(fields: Sequence[SectionField]) -> dict[str, Any]:
    """The reply's shape: each section with its fields, the chief complaint's quotes apart."""
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

_HINTED_MARK = re.compile(r'"(Not (?:discussed|asked|stated)\.)"')
_LEFT_EMPTY = re.compile(r"\bleave (?:it |this |the field )?empty\b", re.IGNORECASE)
_UNCOVERED = frozenset({"", "not discussed", "not asked", "not stated"})


def uncovered_text(f: SectionField) -> str:
    """What a field the visit never touched reads: the words its hint gives for that,
    empty where the hint says to leave it empty, else "Not stated."."""
    hint = f.field.ai_hint or ""
    if found := _HINTED_MARK.search(hint):
        return found.group(1)
    return "" if _LEFT_EMPTY.search(hint) else NOT_STATED


def _is_uncovered(text: str) -> bool:
    return text.strip().rstrip(".").strip().lower() in _UNCOVERED


def compose_chief_complaint(
    text: str, quotes: Any, segments: Mapping[int, str], person: str, uncovered: str
) -> str:
    """The reason for the visit, then each kept quotation framed as the client's.

    A quotation whose cited lines do not hold its words is dropped; the reason
    stands alone, and reads ``uncovered`` when there is none.
    """
    reason = "" if _is_uncovered(text) else text.strip()
    said = [
        f'The {person} said: "{words}"'
        for q in (quotes if isinstance(quotes, list) else [])
        if isinstance(q, dict)
        and (words := verified_words(str(q.get("words") or ""), q.get("segment_ids"), segments))
    ]
    return " ".join(p for p in (reason, *said) if p) or uncovered


def compose(
    reply: Mapping[str, Any],
    fields: Sequence[SectionField],
    segments: Mapping[int, str],
    person: str,
) -> dict[str, dict[str, Any]]:
    """Each field's content by section and key, from the reply."""
    content: dict[str, dict[str, Any]] = {}
    for f in fields:
        raw_section = reply.get(f.section)
        raw = raw_section.get(f.field.key) if isinstance(raw_section, dict) else None
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
            text = str(raw).strip() if raw else ""
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
