# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The extraction call: what this visit said about the fields printed from the chart.

A small structured call beside the draft. It sees the transcript with each
line numbered and each code-written field with the chart's text for it, and
returns, per field, what the visit stated about it (a substance field's
screen too), the diagnoses the clinician named that the problem list lacks,
and whether a telehealth client said they were at home. Every item cites the
numbered lines that say it; an item citing none, or a line this transcript
does not have, is dropped here, as the proposal call drops one. Its only
output is :class:`~app.notes.chart_fields.Statements`: the chart's text and
the marks around it are written in code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..chart_proposals.drafting import cited_evidence, segment_texts
from ..notes.chart_fields import (
    NamedDiagnosis,
    RenderedField,
    Screen,
    Statement,
    Statements,
    chart_text,
)
from ..notes.field_sources import PLACE_OF_SERVICE, PROBLEMS, is_history, is_substance
from .source_attribution_service import format_transcript_with_segment_ids

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ..notes.chart_context import ChartContext

SCHEMA_TITLE = "ChartFieldStatements"

SYSTEM_PROMPT = (
    "You read one visit's transcript and report what was said about fields of a "
    "client's chart. You report only what was said: you never infer, interpret or "
    "add. Return only the JSON object asked for."
)

_SCREEN_BY_NAME: dict[str, Screen] = {
    "stated": "stated",
    "denied": "denied",
    "asked_no_change": "asked_no_change",
}
_SCREENS = list(_SCREEN_BY_NAME)
_EVIDENCE = {"type": "array", "items": {"type": "integer"}}


def _statement_keys(fields: Sequence[RenderedField]) -> list[str]:
    return [f.field.key for f in fields if f.source not in (PROBLEMS, PLACE_OF_SERVICE)]


def _asks_diagnoses(fields: Sequence[RenderedField]) -> bool:
    return any(f.source == PROBLEMS for f in fields)


#: The lines of today's risk screen and safety plan, which are this visit's and never history.
RISK_LINES = "risk_and_safety_plan_segment_ids"


def is_telehealth(inputs: Mapping[str, str]) -> bool:
    return (inputs.get("place_of_service") or "").strip().lower().startswith("tele")


def _asks_at_home(fields: Sequence[RenderedField], inputs: Mapping[str, str]) -> bool:
    return is_telehealth(inputs) and any(f.source == PLACE_OF_SERVICE for f in fields)


def response_schema(fields: Sequence[RenderedField], inputs: Mapping[str, str]) -> dict[str, Any]:
    """The reply's shape: statements always; diagnoses and at-home only when asked."""
    properties: dict[str, Any] = {
        "statements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "field_key": {"type": "string", "enum": _statement_keys(fields)},
                    "screen": {"type": "string", "enum": _SCREENS},
                    "stated": {"type": "string"},
                    "medication": {"type": "string"},
                    "dose": {"type": "string"},
                    "evidence_segment_ids": _EVIDENCE,
                },
                "required": ["field_key", "screen", "stated", "evidence_segment_ids"],
            },
        },
        RISK_LINES: _EVIDENCE,
    }
    if _asks_diagnoses(fields):
        properties["diagnoses"] = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "code": {"type": "string"},
                    "evidence_segment_ids": _EVIDENCE,
                },
                "required": ["label", "evidence_segment_ids"],
            },
        }
    if _asks_at_home(fields, inputs):
        properties["client_at_home"] = {
            "type": "object",
            "properties": {"at_home": {"type": "boolean"}, "evidence_segment_ids": _EVIDENCE},
            "required": ["at_home", "evidence_segment_ids"],
        }
    return {
        "type": "object",
        "title": SCHEMA_TITLE,
        "properties": properties,
        "required": list(properties),
    }


_INSTRUCTIONS = """\
For each field below, report what the {term} or the clinician said in this visit, the \
dictation after the {term}'s last line included, that changes or adds to the chart's text \
shown. Leave out a field the visit did not touch or only repeated.
- stated: the words as said, copied from the cited line, short; never a summary, never a \
bare yes or no. \
evidence_segment_ids: the numbers (n in [Sn]) of the lines that say it; an item no line says \
is not an item.
- A history field is the {term}'s past and circumstances. This visit's symptoms and how \
they affect work or home, today's risk questions and safety plan (who to call in a crisis \
included), and the therapy done in this visit are not history: leave them out. Grief, worry \
or a memory the {term} is working through today is this visit's, not new history.
- A substance field is this visit's screen: denied when the {term} denied use; stated \
(with their words) whenever they described their use, even as the chart has it; \
asked_no_change only when they said nothing had changed. Leave out a substance never asked \
about; a catch-all question ("anything else?") \
asks only about other_substances. Every other field takes stated.
- current_medications: one item per medication the {term} says they take that the chart \
lacks, with the dose as stated, and its name and dose also in medication and dose. A \
medication the clinician starts, stops or changes this visit is not one.
- allergies: an allergy, or a denial of allergies, as said.
- risk_and_safety_plan_segment_ids: every line of today's risk questions, their answers and \
the safety plan."""

_DIAGNOSES = """\
- diagnoses: each diagnosis the clinician names that the problem list lacks, with a code \
only if one was said."""

_AT_HOME = """\
- client_at_home: at_home is true only if the {term} said they are at home for this visit; \
evidence_segment_ids cites every line about where the {term} is for this visit, at home or \
not. Where the {term} is for this visit is not history."""


def build_prompt(
    fields: Sequence[RenderedField],
    chart: ChartContext,
    inputs: Mapping[str, str],
    indexed_transcript: str,
) -> str:
    term = chart.person
    lines = [_INSTRUCTIONS.format(term=term)]
    if _asks_diagnoses(fields):
        lines.append(_DIAGNOSES)
    if _asks_at_home(fields, inputs):
        lines.append(_AT_HOME.format(term=term))
    lines.extend(["", "Fields, with the chart's text for each:"])
    for f in fields:
        if f.source == PLACE_OF_SERVICE:
            continue
        if f.source == PROBLEMS:
            lines.append(f"- Problem list: {chart_text(f.source, chart)}")
            continue
        screen = ", substance screen" if is_substance(f.source) else ""
        lines.append(f"- {f.field.key} ({f.field.label}{screen}): {chart_text(f.source, chart)}")
    lines.extend(["", "Transcript (each line numbered [Sn]):", indexed_transcript])
    return "\n".join(lines)


def parse(
    reply: Mapping[str, Any],
    fields: Sequence[RenderedField],
    inputs: Mapping[str, str],
    segments: Mapping[int, str],
) -> Statements:
    """The reply's items that cite this visit's lines and name a field that was asked.

    A history statement that cites only lines the reply marks as this visit's
    alone (where a telehealth client is for the visit, today's risk screen and
    safety plan) is dropped: those belong to the attestation and the risk
    section, never to the client's history.
    """
    sources = {f.field.key: f.source for f in fields}
    keys = set(_statement_keys(fields))
    whereabouts = _whereabouts(reply, fields, inputs, segments)
    this_visit_only = whereabouts | _risk_lines(reply, segments)
    kept: list[Statement] = []
    raw = reply.get("statements")
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or item.get("field_key") not in keys:
            continue
        evidence = cited_evidence(item.get("evidence_segment_ids"), segments)
        if evidence is None:
            continue
        cited = {e.segment_id for e in evidence}
        source = sources[item["field_key"]]
        if is_history(source) and not is_substance(source) and cited <= this_visit_only:
            continue
        screen: Screen = _SCREEN_BY_NAME.get(str(item.get("screen")), "stated")
        kept.append(
            Statement(
                field_key=str(item["field_key"]),
                screen=screen,
                stated=str(item.get("stated") or "").strip(),
                medication=str(item.get("medication") or "").strip(),
                dose=str(item.get("dose") or "").strip(),
            )
        )
    named: list[NamedDiagnosis] = []
    raw_dx = reply.get("diagnoses") if _asks_diagnoses(fields) else None
    for item in raw_dx if isinstance(raw_dx, list) else []:
        if not isinstance(item, dict) or not str(item.get("label") or "").strip():
            continue
        if cited_evidence(item.get("evidence_segment_ids"), segments) is None:
            continue
        code = str(item.get("code") or "").strip() or None
        named.append(NamedDiagnosis(label=str(item["label"]).strip(), code=code))
    home = reply.get("client_at_home")
    at_home = bool(whereabouts) and isinstance(home, dict) and home.get("at_home") is True
    return Statements(fields=tuple(kept), diagnoses=tuple(named), client_at_home=at_home)


def _whereabouts(
    reply: Mapping[str, Any],
    fields: Sequence[RenderedField],
    inputs: Mapping[str, str],
    segments: Mapping[int, str],
) -> set[int]:
    """The lines the reply says are about where a telehealth client is for this visit."""
    home = reply.get("client_at_home") if _asks_at_home(fields, inputs) else None
    if not isinstance(home, dict):
        return set()
    evidence = cited_evidence(home.get("evidence_segment_ids"), segments)
    return {e.segment_id for e in evidence} if evidence else set()


def _risk_lines(reply: Mapping[str, Any], segments: Mapping[int, str]) -> set[int]:
    """The lines the reply says are today's risk screen and safety plan, those this visit has."""
    raw = reply.get(RISK_LINES)
    return {
        n
        for n in (raw if isinstance(raw, list) else [])
        if isinstance(n, int) and not isinstance(n, bool) and n in segments
    }


def extract_statements(
    complete: Callable[[str, str, dict[str, Any]], dict[str, Any]],
    fields: Sequence[RenderedField],
    chart: ChartContext,
    inputs: Mapping[str, str],
    transcript_content: str,
) -> Statements:
    """Ask what the visit said about ``fields``. Raises what ``complete`` raises:
    a draft without it would say a screen did not happen when it did."""
    indexed = format_transcript_with_segment_ids(transcript_content)
    if not indexed or not fields:
        return Statements()
    reply = complete(
        SYSTEM_PROMPT,
        build_prompt(fields, chart, inputs, indexed),
        response_schema(fields, inputs),
    )
    return parse(reply, fields, inputs, segment_texts(indexed))
