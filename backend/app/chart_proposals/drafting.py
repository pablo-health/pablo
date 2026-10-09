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
import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..chart_history.fields import is_history_key
from ..drug_names.sound_alikes import unconfirmed
from ..notes.chart_context import STATED_THIS_VISIT
from ..notes.chart_fields import chart_text, restates_chart_medication
from ..notes.client_present import MIN_BOUNDARY_CLIENT_WORDS, is_client_speaker
from ..notes.field_sources import MEDICATIONS
from ..services.source_attribution_service import format_transcript_with_segment_ids
from .evidence import cited_evidence, segment_texts
from .families import (
    FAMILIES,
    HEADING,
    NOTE_FIELD_CHART_KEYS,
    STATED_IN,
    chart_key_for,
    family_for,
)
from .materiality import CitedLine, OtherFields, Speaker, admit
from .medication_mentions import (
    KEPT_REASONS,
    MEDICATIONS_KEPT,
    kept_medications,
    medications_to_decide,
)
from .models import Considered, Drafted, DraftedProposal, Origin
from .recorded import field_text

if TYPE_CHECKING:
    from ..models import Transcript
    from ..notes.chart_context import ChartContext
    from ..notes.chart_fields import Statements
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
                    "stated_in": {"type": "string", "enum": list(STATED_IN)},
                },
                "required": ["field_key", "proposed_text", "what_changed", "evidence_segment_ids"],
            },
        }
    }
    for family in FAMILIES:
        if family.reply_key is not None:
            properties[family.reply_key] = {"type": "array", "items": family.reply_item_schema()}
    properties[MEDICATIONS_KEPT] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "drug_name": {"type": "string"},
                "reason": {"type": "string", "enum": list(KEPT_REASONS)},
                "note": {"type": "string"},
            },
            "required": ["drug_name", "reason"],
        },
    }
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
to it. If nothing changed, return an empty list: that is the usual answer.

A history field changes only when the chart would read differently at the next visit in a \
way that matters: a new lasting fact (a new treatment the client now has, such as weekly \
therapy begun since the last visit), a correction, or something that is no longer true \
(the field still keeps it, and says what replaced it: "Married; separated in January, \
divorce finalized in May."). These are not changes: a \
restatement, or a detail that only confirms or fills in what the field already says; an \
event that leaves nothing lasting behind (an appointment, a referral, an interview, a test \
result, a visit, a meal, a weekend); something considered, discussed or planned for later; \
and something said while answering about another field. For example:
- Change: "The practice closed in March and I've been laid off since." (work_school)
- Change: "We separated in January and I moved into an apartment." (relationships)
- Change: "My doctor diagnosed sleep apnea; I use a CPAP every night now." (medical_history)
- Not a change: "He's been good about it, he keeps saying we're fine." (relationships)
- Not a change: "My son keeps pushing me, so I booked a therapy intake for next week." \
(psychotherapy_history)
- Not a change: "Primary care checked my thyroid in January and it was normal." \
(medical_history)

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
here. Propose an update to a chart field for each lasting fact about the client the \
document states that the chart does not already say. A field the document says nothing \
about, or says only what the chart already says, needs no proposal, and neither do the \
visit's own details: where it took place, where each person joined from, how long it was.

A note often carries forward blocks written at earlier visits, and a carried block can be \
out of date. Where two parts of the document disagree about the same thing (a carried list \
of current medications and the plan, say), propose what the plan states, or else the part \
written for this visit, and cite the paragraph that disagrees as well, so the clinician \
sees the conflict. Such a proposal always cites at least two paragraphs: the one it follows \
and the one that disagrees with it. This holds for every field: a history field whose \
carried block says one thing and whose interval history says another cites both, as a \
medication whose carried list and plan differ does.

For the medication list, the document's plan is the clinician's decision at that visit. A \
medication the plan starts is a start, one it stops is a stop, and one whose dose or \
frequency it changes is a change, citing the plan's paragraph, and the carried list's too \
where it shows the medication otherwise. A medication the client takes that the list lacks \
and the plan continues is an add. A medication only a carried block lists, which the plan \
and the part written for this visit do not mention, gets no item at all, not even an add: \
it may have been stopped since that block was written.

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


_DOCUMENT_PRECEDENCE = """\
For a document, where the rules for the fields read otherwise, these hold. A medication \
named only in a block carried forward, and not in the plan or the part written for this \
visit, is not known to be taken now: its stated_in is "a carried block only". One the plan \
continues that the list lacks is an add, never a start. A proposal about a field or a \
medication that a carried block also states cites that carried paragraph as well as the one \
it follows. The document's heading paragraph (who was seen, when, how and from where each \
person joined) is about the visit, not the client: a proposal only it states has stated_in \
"the heading"."""


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


def _proposable_key(note_field_key: str) -> str | None:
    """The chart field a note field prints, when a proposal can name it."""
    return (
        chart_key_for(note_field_key)
        or NOTE_FIELD_CHART_KEYS.get(note_field_key)
        or (note_field_key if family_for(note_field_key) else None)
    )


def _said_this_visit(statements: Statements, chart: ChartContext) -> list[str]:
    """What the extraction found said this visit about the chart's fields, each beside
    the chart's text for it, as the draft prints it, and with the lines that say it.
    A substance the client denied is said too; one asked about with no change, and a
    medication the chart already lists at that dose, are not, as the draft marks
    neither. The medication list is shown in full above, so its line is not repeated."""
    found = []
    for s in statements.fields:
        chart_key = _proposable_key(s.field_key)
        if chart_key is None or s.screen == "asked_no_change":
            continue
        if s.screen == "stated" and (not s.stated or restates_chart_medication(s, chart)):
            continue
        said = "denied" if s.screen == "denied" else f'"{s.stated}"'
        lines = ", ".join(f"S{n}" for n in s.segment_ids)
        has = "" if chart_key == MEDICATIONS else f"chart has {chart_text(chart_key, chart)}; "
        found.append(f"- {chart_key}: {has}said {said}" + (f" [{lines}]" if lines else ""))
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
    to_decide: Sequence[str] = (),
    statements: Statements | None = None,
) -> str:
    """The proposal prompt: the chart, the rules, and the numbered transcript.
    ``to_decide`` are listed medications the call must decide on, one by one.

    What was said this visit about the chart's fields comes from ``statements``,
    the draft's extraction, with the lines that say it. Without it the fields
    ``draft`` marks as stated this visit are listed instead."""
    parts = _chart_and_rules(chart, _INSTRUCTIONS)
    said = _said_this_visit(statements, chart) if statements is not None else []
    if said:
        parts.extend(
            [
                "",
                (
                    "What this visit said about these chart fields, beside what the chart "
                    + "has, with the lines that say it. Most only repeat or fill in the chart, "
                    + "or are details for the note, not changes to it: propose one only where "
                    + "it is a change by the rules above, citing those lines:"
                ),
                *said,
            ]
        )
    stated = _stated_this_visit(draft or {}) if statements is None else []
    if stated:
        parts.extend(
            [
                "",
                (
                    "The draft of this visit's note marks these as stated this visit. Most are "
                    + "details for the note, not changes to the chart: propose one only where it "
                    + "is a change by the rules above, citing the transcript lines it came from:"
                ),
                *stated,
            ]
        )
    if to_decide:
        parts.extend(
            [
                "",
                (
                    "The transcript names these listed medications near a different dose or a "
                    + "word like stopped. Decide each one: put its change in "
                    + "medication_changes, or, if the visit leaves it as listed, put it in "
                    + "medications_kept with the reason (a client's own stop the clinician has "
                    + "not addressed this visit is left as listed):"
                ),
                *(f"- {name}" for name in to_decide),
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
    parts.extend(["", _DOCUMENT_PRECEDENCE])
    parts.extend(["", "Document (each paragraph numbered [Sn]):", _indexed(segments)])
    return "\n".join(parts)


def _heard_as_another(proposal: DraftedProposal, chart: ChartContext) -> bool:
    """A medication change whose drug the transcript may have heard in place of a listed
    one (:func:`app.drug_names.sound_alikes.unconfirmed`): the draft marks it for the
    clinician, and it is never proposed. A document is typed, so it is not asked."""
    change = proposal.change
    if change is None:
        return False
    cited = [e.text for e in proposal.evidence]
    return unconfirmed(change.drug_name, cited, chart) is not None


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
        if family is None or item.get("stated_in") == HEADING:
            continue
        evidence = cited_evidence(item.get("evidence_segment_ids"), segments)
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
            evidence = cited_evidence(item.get("evidence_segment_ids"), segments)
            drafted = family.drafted(item, evidence, chart) if evidence is not None else None
            if drafted is None or (origin == "transcript" and _heard_as_another(drafted, chart)):
                continue
            identity = (drafted.field_key, drafted.item_key.lower())
            if identity in seen or not family.admits(drafted, drafted.proposed_text, chart):
                continue
            seen.add(identity)
            kept.append(replace(drafted, origin=origin))
    return kept


_SPEAKER_LINE = re.compile(r"^\[[\d:]+\]\s*([^:]+):\s*(.*)$", re.S)


def cited_lines(segments: Mapping[int, str]) -> dict[int, CitedLine]:
    """Each transcript line's words and who said them: the client; the clinician after
    the client's last line (their dictation, anything dictated after the recording
    included); or the clinician before it (the interview). A transcript with no client
    line of a few words cannot tell the clinician's questions from their dictation, so
    every line of it is ``unknown``."""
    parsed: dict[int, tuple[str, str]] = {}
    for n, text in segments.items():
        match = _SPEAKER_LINE.match(text.strip())
        parsed[n] = (match.group(1).strip(), match.group(2)) if match else ("", text)
    client_lines = [
        n
        for n, (speaker, said) in parsed.items()
        if is_client_speaker(speaker) and len(said.split()) >= MIN_BOUNDARY_CLIENT_WORDS
    ]
    last = max(client_lines, default=None)
    lines: dict[int, CitedLine] = {}
    for n, (speaker, said) in parsed.items():
        if last is None:
            who: Speaker = "unknown"
        elif is_client_speaker(speaker):
            who = "client"
        else:
            who = "dictated" if n > last else "interview"
        lines[n] = CitedLine(said, who)
    return lines


def said_this_visit(
    extracted: Collection[str],
    statements: Statements | None,
    draft: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    """What the extraction beside the draft found said this visit, for each chart field
    it reads the visit for (``extracted``), empty for one it found nothing about: from
    ``statements``, or without them (a retry) from the marks the draft printed from
    them. A field asked about with no change has nothing said."""
    said: dict[str, list[str]] = {key: [] for key in extracted}
    if statements is not None:
        for s in statements.fields:
            key = _proposable_key(s.field_key)
            if key in said and s.screen != "asked_no_change":
                said[key].append("denied" if s.screen == "denied" else s.stated)
        return said
    for line in _stated_this_visit(draft or {}):
        key, _, text = line.removeprefix("- ").partition(": ")
        if key in said:
            said[key].append(text)
    return said


def material(
    proposals: Sequence[DraftedProposal],
    chart: ChartContext,
    segments: Mapping[int, str],
    said: Mapping[str, Sequence[str]] | None = None,
) -> tuple[list[DraftedProposal], tuple[Considered, ...]]:
    """The proposals worth offering, and the history proposals considered and not
    offered, each with the reason (:mod:`.materiality`). The medication list and the
    allergies keep their families' rules and pass through. ``said`` is what the
    extraction found said this visit (:func:`said_this_visit`), keyed by the fields it
    reads the visit for; a transcript proposal to one of them is checked against it."""
    lines = cited_lines(segments)
    recorded = {f.key: f.text for f in chart.history}
    medications = [
        *(m.name for m in chart.medications),
        *(p.item_key for p in proposals if p.field_key == MEDICATIONS),
    ]
    offered: list[DraftedProposal] = []
    considered: list[Considered] = []
    for proposal in proposals:
        if not is_history_key(proposal.field_key):
            offered.append(proposal)
            continue
        others = OtherFields(
            history={k: t for k, t in recorded.items() if k != proposal.field_key},
            problems=[p.label for p in chart.problems],
            medications=medications,
        )
        spoken = proposal.origin == "transcript"
        cited = [
            lines[e.segment_id] if spoken else CitedLine(e.text, "unknown")
            for e in proposal.evidence
            if e.segment_id in lines or not spoken
        ]
        heard = (said or {}).get(proposal.field_key) if spoken else None
        verdict = admit(proposal, recorded.get(proposal.field_key, ""), heard, cited, others)
        if verdict.reason is None:
            offered.append(proposal)
            continue
        logger.info(
            "Chart proposal considered, not offered: %s (%s)", proposal.field_key, verdict.reason
        )
        considered.append(
            Considered(
                field_key=proposal.field_key,
                proposed_text=proposal.proposed_text,
                what_changed=proposal.what_changed,
                evidence_segment_ids=tuple(e.segment_id for e in proposal.evidence),
                origin=proposal.origin,
                reason=verdict.reason,
            )
        )
    return offered, tuple(considered)


def screened(
    drafted: Drafted,
    chart: ChartContext,
    segments: Mapping[int, str],
    said: Mapping[str, Sequence[str]] | None = None,
) -> Drafted:
    """``drafted`` with its history proposals checked for materiality (:func:`material`),
    those not offered kept as ``considered``. A failed call is returned as it is."""
    if drafted.error_class:
        return drafted
    offered, considered = material(drafted.proposals, chart, segments, said)
    return replace(drafted, proposals=offered, considered=considered)


def transcript_segments(transcript: Transcript) -> dict[int, str]:
    """The transcript's lines, numbered as the proposal call numbers them."""
    return segment_texts(format_transcript_with_segment_ids(transcript.content))


def propose_chart_updates(
    complete: CompleteStructured,
    chart: ChartContext,
    transcript: Transcript,
    *,
    draft: Mapping[str, Any] | None = None,
    statements: Statements | None = None,
) -> Drafted:
    """Ask what this visit changes on the chart. Never raises: a failure is
    returned with its exception type, so the note can say it was not checked.
    ``statements`` is the draft's extraction (see :func:`build_prompt`).

    The listed medications the transcript names near a different dose or a word
    saying they were stopped are put to the call to decide one by one."""
    indexed = format_transcript_with_segment_ids(transcript.content)
    if not indexed:
        return Drafted([])
    segments = segment_texts(indexed)
    to_decide = medications_to_decide(chart, segments)

    def parse(reply: Mapping[str, Any]) -> Drafted:
        proposals = parse_proposals(reply, chart, segments)
        kept = kept_medications(reply, chart, to_decide, proposals)
        return Drafted(proposals, to_decide=tuple(to_decide), kept=kept)

    prompt = build_prompt(chart, indexed, draft=draft, to_decide=to_decide, statements=statements)
    return _ask(complete, prompt, parse)


def propose_from_document(complete: CompleteStructured, chart: ChartContext, text: str) -> Drafted:
    """Ask what an imported note adds to the chart, citing its paragraphs. Never raises."""
    segments = document_segments(text)
    if not segments:
        return Drafted([])

    def parse(reply: Mapping[str, Any]) -> Drafted:
        return Drafted(parse_proposals(reply, chart, segments, origin="document"))

    return _ask(complete, build_document_prompt(chart, segments), parse)


def _ask(
    complete: CompleteStructured, prompt: str, parse: Callable[[Mapping[str, Any]], Drafted]
) -> Drafted:
    try:
        reply = complete(SYSTEM_PROMPT, prompt, RESPONSE_SCHEMA)
        return parse(reply)
    except Exception as exc:
        logger.warning("Chart proposal call failed; the note has no proposals", exc_info=True)
        return Drafted([], error_class=type(exc).__name__)
