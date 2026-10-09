# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The risk, mental status and measures call: those sections drafted on their own.

A small structured call beside the main draft and the extraction. It sees the
transcript with each line numbered and the fields of the sections routed to it
(:mod:`app.notes.section_calls`) with their hints, and returns each field's
text. A risk field comes back in two parts: the clinician's finding, and the
client's own words, each with the question asked and the numbered lines they
are copied from. The final text is composed here: the finding as returned,
then each quotation framed as the client's, kept only when every line it cites
contains those words, and then copied from the line. A quotation that fails
the check is dropped, and the field says what was asked, unquoted.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..chart_proposals.drafting import cited_evidence, segment_texts
from .source_attribution_service import format_transcript_with_segment_ids

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..notes.section_calls import SectionField

SCHEMA_TITLE = "RiskMentalStatusSections"

SYSTEM_PROMPT = (
    "You draft the risk assessment, mental status exam and measures of a clinician's "
    "note from one visit's transcript. You write only what the visit says: you never "
    "infer, judge or add. Return only the JSON object asked for."
)

NOT_STATED = "Not stated."

_INSTRUCTIONS = """\
Draft each field below from this visit. The clinician's dictation after the {term}'s last \
line is the clinician's own and the first source for these fields.
- Write the clinician's findings as findings: declarative, in the clinician's words, with no \
quotation marks, never saying who said, asked, noted or dictated them.
- A level, score or judgment only as the clinician stated it; never one of your own.
- A field with quotes: text is the clinician's finding alone, never a quotation. Each quote \
is the {term}'s own answer about suicide, self-harm or violence: asked is the question \
("Asked about thoughts of harming self or others"), words are copied exactly from one \
{term} line, segment_ids is that line's n in [Sn]. Never quote the clinician. Quotes belong \
only where the field records thoughts or acts of harm; protective factors, the overall risk \
and the safety plan are the clinician's findings, with none.
- The safety plan only if ideation, self-harm or violence was reported, else empty. Advice \
or crisis numbers given to the {term} are not a finding about self-harm.
- "Not stated." for a field the visit did not cover, unless its hint says to leave it empty."""

_EVIDENCE = {"type": "array", "items": {"type": "integer"}}
_QUOTED_FIELD: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "asked": {"type": "string"},
                    "words": {"type": "string"},
                    "segment_ids": _EVIDENCE,
                },
                "required": ["asked", "words", "segment_ids"],
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
    """The reply's shape: each section with its fields, a risk field's quotes apart."""
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

_CURLY_SINGLE = chr(0x2018) + chr(0x2019)
_CURLY_DOUBLE = chr(0x201C) + chr(0x201D)
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|" + chr(0x2026) + r")\s*")
_APOSTROPHES = "'" + _CURLY_SINGLE
_OUTER = "\"' " + _CURLY_SINGLE + _CURLY_DOUBLE


def _pattern(part: str) -> re.Pattern[str] | None:
    """A case-blind pattern for ``part`` that lets spacing and apostrophes differ."""
    tokens = part.split()
    if not tokens:
        return None
    escaped = [
        "".join(f"[{_APOSTROPHES}]" if c in _APOSTROPHES else re.escape(c) for c in t)
        for t in tokens
    ]
    return re.compile(r"\s+".join(escaped), re.IGNORECASE)


def verified_words(words: str, segment_ids: Any, segments: Mapping[int, str]) -> str | None:
    """The quoted words as the cited line has them, or ``None``.

    ``None`` when the quote cites no line, a line this transcript lacks, or a
    line that does not contain every part of the words (an ellipsis splits
    them). The words returned are the line's own, so spacing, case and
    apostrophes are the transcript's.
    """
    evidence = cited_evidence(segment_ids, segments)
    if evidence is None:
        return None
    parts = [p.strip(_OUTER) for p in _ELLIPSIS.split(words.strip().strip(_OUTER))]
    patterns = [_pattern(p) for p in parts if p]
    if not patterns or any(p is None for p in patterns):
        return None
    found: list[str] = []
    for i, e in enumerate(evidence):
        matches = [p.search(e.text) for p in patterns if p is not None]
        if not all(matches):
            return None
        if i == 0:
            found = [m.group(0) for m in matches if m is not None]
    return " ... ".join(found).replace('"', "'")


def _asked(raw: Any) -> str:
    asked = str(raw or "").strip().rstrip(".,:;").strip()
    return asked[:1].upper() + asked[1:]


def compose_quoted(text: str, quotes: Any, segments: Mapping[int, str], person: str) -> str:
    """A risk field's text: the finding, then each kept quotation framed as the client's.

    A quotation whose cited lines do not hold its words is dropped, and the
    question it answered is written in its place, unquoted.
    """
    finding = text.strip()
    parts: list[str] = []
    kept = False
    for q in quotes if isinstance(quotes, list) else []:
        if not isinstance(q, dict):
            continue
        asked = _asked(q.get("asked"))
        words = verified_words(str(q.get("words") or ""), q.get("segment_ids"), segments)
        if words:
            kept = True
            frame = f"{asked}, the {person} said" if asked else f"The {person} said"
            parts.append(f'{frame}: "{words}"')
        elif asked:
            parts.append(f"{asked}.")
    if kept and finding.rstrip(".").lower() == NOT_STATED.rstrip(".").lower():
        finding = ""
    return " ".join(p for p in (finding, *parts) if p)


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
            value = compose_quoted(
                str(parts.get("text") or ""), parts.get("quotes"), segments, person
            )
        elif f.field.kind == "list":
            value = [str(item).strip() for item in raw if item] if isinstance(raw, list) else []
        else:
            value = str(raw).strip() if raw else ""
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
    indexed = format_transcript_with_segment_ids(transcript_content)
    if not fields:
        return {}
    reply = complete(
        SYSTEM_PROMPT,
        build_prompt(fields, person, indexed, current_note),
        response_schema(fields),
    )
    return compose(reply, fields, segment_texts(indexed) if indexed else {}, person)
