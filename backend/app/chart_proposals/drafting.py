# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The proposal call: what this visit changes on the chart.

A second, smaller structured call after the draft, the same pattern as SOAP's
source attribution. It is given the chart (every proposable field, empty ones
included) and the visit's transcript with each line numbered, the clinician's
dictated addendum included, and returns a list of changes. Each change cites
the numbered lines that state it. The ids are checked here against this
transcript and a proposal citing nothing, or a line the transcript does not
have, is dropped: an unsupported proposal never reaches the clinician. No
text is matched against the transcript; the evidence kept is the cited
lines' own text.

A note imported from another records system has no transcript; the
document is read in its place, each paragraph numbered as a line is, so a
proposal from it cites the paragraphs that say it, checked the same way.

The call never fails the draft. An error leaves the note with no proposals
and is recorded as the note's run having failed, so the clinician is told
the note was not checked rather than shown an empty list.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..notes.chart_context import STATED_THIS_VISIT
from ..services.source_attribution_service import format_transcript_with_segment_ids
from .families import FAMILIES, NOTE_FIELD_CHART_KEYS, chart_key_for, family_for
from .models import Drafted, DraftedProposal, Evidence, Origin
from .recorded import field_text

if TYPE_CHECKING:
    from ..models import Transcript
    from ..notes.chart_context import ChartContext
    from ..services.note_generation_service import CompleteStructured

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You compare a client's chart with what was said in one visit and propose updates to "
    "the chart where the visit changed something. You state only what was said: you never "
    "infer, interpret or add. Return only the JSON object asked for."
)


def _response_schema() -> dict[str, Any]:
    """``proposals`` for the free-text families, and a list per structured family."""
    text_keys = [key for f in FAMILIES if f.reply_key is None for key in f.field_keys()]
    properties: dict[str, Any] = {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_key": {"type": "string", "enum": text_keys},
                    "entry": {"type": "string"},
                    "proposed_text": {"type": "string"},
                    "what_changed": {"type": "string"},
                    "evidence_segment_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["field_key", "proposed_text", "what_changed", "evidence_segment_ids"],
            },
        }
    }
    for family in FAMILIES:
        if family.reply_key is not None:
            properties[family.reply_key] = {"type": "array", "items": family.reply_item_schema()}
    return {
        "type": "object",
        "title": "ChartProposals",
        "properties": properties,
        "required": list(properties),
    }


RESPONSE_SCHEMA: dict[str, Any] = _response_schema()

_INSTRUCTIONS = """\
Propose an update to a chart field only when the transcript, including anything the \
clinician dictated after the client's last line, states something that changes it or adds \
to it. If nothing changed, return an empty list: that is the usual answer. A client \
restating what the chart already says, something only considered or discussed, and a plan \
for later are not changes.

Refer to the person seen as "the {term}" or with they/them; never he, she, his or her, \
unless the chart records their pronouns.

For each change give:
- field_key: the field's key as listed above.
- proposed_text: the text the field should hold once updated.
- what_changed: one short line saying what is new.
- evidence_segment_ids: the numbers (n in [Sn]) of the transcript lines that state the \
change. Cite only lines that say it. A change no line states is not a change.
- entry: only for a list field, the entry the change is about (for allergies, the \
substance)."""

_DOCUMENT_INSTRUCTIONS = """\
The document is a note about this client written in another records system and imported \
here. Propose an update to a chart field for each thing the document states that the chart \
does not already say. A field the document says nothing about, or says only what the chart \
already says, needs no proposal.

A note often carries forward blocks written at earlier visits, and a carried block can be \
out of date. Where two parts of the document disagree about the same thing (a carried list \
of current medications and the plan, say), propose what the plan states, or else the part \
written for this visit, and cite the paragraph that disagrees as well, so the clinician \
sees the conflict. Such a proposal always cites at least two paragraphs: the one it follows \
and the one that disagrees with it.

The diagnoses in the document's assessment are this visit's and reach the chart's problem \
list from the note itself: never propose them to a history field. prior_diagnoses is for \
diagnoses the document says were given before.

Refer to the person seen as "the {term}" or with they/them; never he, she, his or her, \
unless the chart records their pronouns, whatever the document uses.

For each change give:
- field_key: the field's key as listed above.
- proposed_text: the text the field should hold once updated, in the document's words.
- what_changed: one short line saying what is new.
- evidence_segment_ids: the numbers (n in [Sn]) of the document paragraphs that state it. \
Cite only paragraphs that say it. A change no paragraph states is not a change.
- entry: only for a list field, the entry the change is about (for allergies, the \
substance)."""


def _stated_this_visit(draft: Mapping[str, Any]) -> list[str]:
    """The draft's chart-fed fields that mark something as stated this visit."""
    marker = STATED_THIS_VISIT.rstrip(")")
    found = []
    for section in draft.values():
        if not isinstance(section, Mapping):
            continue
        for key, value in section.items():
            text = field_text(value)
            if marker not in text:
                continue
            chart_key = (
                chart_key_for(key)
                or NOTE_FIELD_CHART_KEYS.get(key)
                or (key if family_for(key) else None)
            )
            if chart_key is not None:
                found.append(f"- {chart_key}: {text}")
    return found


def _chart_and_rules(chart: ChartContext, instructions: str) -> list[str]:
    parts = ["The client's chart, as the clinician recorded it:", ""]
    for family in FAMILIES:
        parts.extend(family.chart_lines(chart))
        parts.append("")
    parts.append(instructions.format(term=chart.person))
    parts.append("")
    parts.append("Rules for the fields:")
    for family in FAMILIES:
        parts.extend(family.rules())
    return parts


def build_prompt(
    chart: ChartContext,
    indexed_transcript: str,
    *,
    draft: Mapping[str, Any] | None = None,
) -> str:
    """The proposal prompt: the chart, the rules, and the numbered transcript."""
    parts = _chart_and_rules(chart, _INSTRUCTIONS)
    stated = _stated_this_visit(draft or {})
    if stated:
        parts.extend(
            [
                "",
                (
                    "The draft of this visit's note marks these as stated this visit. Each is "
                    + "expected to need a proposal, citing the transcript lines it came from:"
                ),
                *stated,
            ]
        )
    parts.extend(["", "Transcript (each line numbered [Sn]):", indexed_transcript])
    return "\n".join(parts)


def document_segments(text: str) -> dict[int, str]:
    """An imported document's paragraphs, numbered from 0.

    Paragraphs are separated by blank lines. A document with none (a Word
    export puts each paragraph on its own line) has a paragraph a line.
    """
    lines = [line.strip() for line in text.strip().splitlines()]
    if "" not in lines:
        return dict(enumerate(line for line in lines if line))
    paragraphs: list[str] = []
    current: list[str] = []
    for line in [*lines, ""]:
        if line:
            current.append(line)
        elif current:
            paragraphs.append("\n".join(current))
            current = []
    return dict(enumerate(paragraphs))


def _indexed(segments: Mapping[int, str]) -> str:
    """Each paragraph as ``[Sn]`` and its first line, its other lines indented beneath."""
    return "\n".join(
        f"[S{n}] " + text.replace("\n", "\n    ") for n, text in sorted(segments.items())
    )


def build_document_prompt(chart: ChartContext, segments: Mapping[int, str]) -> str:
    """The proposal prompt for an imported note: the chart, the rules, the numbered document."""
    parts = _chart_and_rules(chart, _DOCUMENT_INSTRUCTIONS)
    parts.extend(["", "Document (each paragraph numbered [Sn]):", _indexed(segments)])
    return "\n".join(parts)


def _segment_texts(indexed_transcript: str) -> dict[int, str]:
    texts = {}
    for line in indexed_transcript.splitlines():
        head, _, rest = line.partition("] ")
        texts[int(head.removeprefix("[S"))] = rest
    return texts


def _evidence(raw: Any, segments: Mapping[int, str]) -> tuple[Evidence, ...] | None:
    """The cited segments, or ``None`` when the proposal cites none or one this visit lacks."""
    if not isinstance(raw, list) or not raw:
        return None
    ids: list[int] = []
    for value in raw:
        if not isinstance(value, int) or isinstance(value, bool) or value not in segments:
            return None
        if value not in ids:
            ids.append(value)
    return tuple(Evidence(segment_id=i, text=segments[i]) for i in sorted(ids))


def parse_proposals(
    reply: Mapping[str, Any],
    chart: ChartContext,
    segments: Mapping[int, str],
    *,
    origin: Origin = "transcript",
) -> list[DraftedProposal]:
    """The reply's proposals that cite these segments and that their field can take."""
    kept: list[DraftedProposal] = []
    seen: set[tuple[str, str]] = set()
    raw = reply.get("proposals")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        field_key = str(item.get("field_key") or "")
        family = family_for(field_key)
        if family is None:
            continue
        evidence = _evidence(item.get("evidence_segment_ids"), segments)
        if evidence is None:
            continue
        proposal = DraftedProposal(
            field_key=field_key,
            item_key=str(item.get("entry") or "").strip() if family.itemized else "",
            proposed_text=str(item.get("proposed_text") or "").strip(),
            what_changed=str(item.get("what_changed") or "").strip(),
            evidence=evidence,
            origin=origin,
        )
        identity = (proposal.field_key, proposal.item_key.lower())
        if identity in seen or not family.admits(proposal, proposal.proposed_text, chart):
            continue
        seen.add(identity)
        kept.append(proposal)
    for family in FAMILIES:
        items = reply.get(family.reply_key) if family.reply_key is not None else None
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            evidence = _evidence(item.get("evidence_segment_ids"), segments)
            drafted = family.drafted(item, evidence, chart) if evidence is not None else None
            if drafted is None:
                continue
            identity = (drafted.field_key, drafted.item_key.lower())
            if identity in seen or not family.admits(drafted, drafted.proposed_text, chart):
                continue
            seen.add(identity)
            kept.append(replace(drafted, origin=origin))
    return kept


def propose_chart_updates(
    complete: CompleteStructured,
    chart: ChartContext,
    transcript: Transcript,
    *,
    draft: Mapping[str, Any] | None = None,
) -> Drafted:
    """Ask what this visit changes on the chart. Never raises: a failure is
    returned with its exception type, so the note can say it was not checked."""
    indexed = format_transcript_with_segment_ids(transcript.content)
    if not indexed:
        return Drafted([])
    prompt = build_prompt(chart, indexed, draft=draft)
    return _ask(complete, prompt, chart, _segment_texts(indexed), "transcript")


def propose_from_document(complete: CompleteStructured, chart: ChartContext, text: str) -> Drafted:
    """Ask what an imported note adds to the chart, citing its paragraphs. Never raises."""
    segments = document_segments(text)
    if not segments:
        return Drafted([])
    return _ask(complete, build_document_prompt(chart, segments), chart, segments, "document")


def _ask(
    complete: CompleteStructured,
    prompt: str,
    chart: ChartContext,
    segments: Mapping[int, str],
    origin: Origin,
) -> Drafted:
    try:
        reply = complete(SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA)
        return Drafted(parse_proposals(reply, chart, segments, origin=origin))
    except Exception as exc:
        logger.warning("Chart proposal call failed; the note has no proposals", exc_info=True)
        return Drafted([], error_class=type(exc).__name__)
