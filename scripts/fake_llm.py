# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in for the model behind availability parsing, for the end-to-end stack.

A clinician describes their hours in a sentence and a model turns it into
rules. The stack has no model credentials, and a spec wants the same reading
every run, so the backend's ``AVAILABILITY_PARSE_BASE_URL`` points here. Each
call is ``POST /v1/structured`` with the prompts the backend would have sent
the model; the reply is the JSON object a model would have returned, in the
flat shape the parse schema asks for (``day_of_week``, ``start`` and ``end``
on the proposal itself, not nested). The backend validates it exactly as it
validates a model's reply, so a reply here that the schema would reject is
refused there too.

Readings are keyed by the sentence a spec types, compared without case,
surrounding space or a trailing full stop. A sentence with no reading here is
answered the way a model answers one it cannot pin down — no proposals and a
question back — rather than with an error, so a spec that strays sees the
product's own refusal instead of a server error.

Run locally with ``uvicorn scripts.fake_llm:app --port 8083``; the compose
stack builds it from ``scripts/e2e/fake-llm.Dockerfile``.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable

app = FastAPI(title="fake-llm")

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _working_hours(day: int, start: str, end: str) -> dict[str, Any]:
    return {
        "rule_type": "working_hours",
        "enforcement": "hard",
        "day_of_week": day,
        "start": start,
        "end": end,
        "human_summary": f"{_DAY_NAMES[day]}, {start} to {end}",
        "confidence": 0.95,
    }


#: The sentences the specs type, and the reply to each.
READINGS: dict[str, dict[str, Any]] = {
    "9 to 5 monday to thursday": {
        "proposals": [_working_hours(day, "09:00", "17:00") for day in range(4)],
        "could_not_parse": None,
        "exclusive": True,
    },
}

UNKNOWN = {
    "proposals": [],
    "could_not_parse": "Which days, and what times?",
    "refusal_reason": "ambiguous",
}

#: Sentences whose odd-numbered calls go wrong before the reading comes
#: back, so a spec can show that a failed or stalled call is handed over
#: rather than waited out. A parse makes two calls here, the bad one and
#: the one that answers, so the next parse of the same sentence starts on
#: a bad one again.
FAILS_FIRST = "10 to 6 monday to thursday"
STALLS_FIRST = "8 to 4 monday to thursday"

#: Longer than the backend waits for any one call.
STALL_SECONDS = 30.0

READINGS[FAILS_FIRST] = {
    "proposals": [_working_hours(day, "10:00", "18:00") for day in range(4)],
    "could_not_parse": None,
    "exclusive": True,
}
READINGS[STALLS_FIRST] = {
    "proposals": [_working_hours(day, "08:00", "16:00") for day in range(4)],
    "could_not_parse": None,
    "exclusive": True,
}

_calls: Counter[str] = Counter()


class StructuredCall(BaseModel):
    """What the backend sends; only the sentence decides the reply."""

    model: str = ""
    system_prompt: str = ""
    user_prompt: str


def _key(sentence: str) -> str:
    return " ".join(sentence.lower().split()).rstrip(".")


@app.post("/v1/structured")
async def structured(call: StructuredCall) -> dict[str, Any]:
    key = _key(call.user_prompt)
    if key in (FAILS_FIRST, STALLS_FIRST):
        _calls[key] += 1
        if _calls[key] % 2 == 1:
            if key == FAILS_FIRST:
                raise HTTPException(status_code=503, detail="model unavailable")
            await asyncio.sleep(STALL_SECONDS)
    return {"data": READINGS.get(key, UNKNOWN), "finish_reason": "stop"}


#: A transcript carrying this line is refused, so a spec can watch a draft fail.
#: The refusal is not one the backend retries: the session is marked failed at once.
REFUSES_DRAFT = "The stand-in will not draft this session."

#: A transcript carrying this line is answered by the fallback model alone:
#: every other model is unavailable, as a provider outage would leave it.
PRIMARY_DOWN = "The first drafting model is down for this session."

#: The fallback the stack names for note drafting in AI_FALLBACKS.
FALLBACK_MODEL = "stand-in-fallback"


class NoteCall(BaseModel):
    """A note draft request; the response schema decides the reply's shape."""

    response_schema: dict[str, Any]
    user_prompt: str = ""
    model: str = ""


def _stand_in(schema: dict[str, Any], path: str) -> Any:
    """A value of the schema's shape, the same for the same schema every run.

    Text says which field it fills, so a spec can find it; a list of text
    holds one such entry; a list of stated diagnoses holds one diagnosis
    with a code; any other list of objects (the sentence-to-transcript
    links a SOAP draft asks for next) is empty, which a model may also say.
    """
    kind = schema.get("type")
    if kind == "object":
        return {
            name: _stand_in(sub, f"{path}.{name}" if path else name)
            for name, sub in schema.get("properties", {}).items()
        }
    if kind == "array":
        items = schema.get("items", {})
        if items.get("title") == "StatedDiagnosis":
            return [{"label": f"Stand-in diagnosis for {path}", "code": "F00.0", "status": ""}]
        return [] if items.get("type") == "object" else [_stand_in(items, path)]
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    return f"Stand-in draft for {path}."


#: The note type proposed for any derive call. Its labels and hints are the
#: stand-in's own words, so nothing a spec sends as a sample is repeated in it.
DERIVED_PROPOSAL: dict[str, Any] = {
    "label": "Follow-up visit",
    "description": "A short follow-up note in three parts.",
    "system_prompt": "Write in brief clinical prose, third person, past tense.",
    "sections": [
        {
            "key": "interval",
            "label": "Interval",
            "fields": [
                {
                    "key": "interval_history",
                    "label": "Interval history",
                    "kind": "text",
                    "ai_hint": "Changes reported since the previous visit.",
                },
                {
                    "key": "current_medications",
                    "label": "Current medications",
                    "kind": "list",
                    "ai_hint": "Each medication as reviewed today.",
                },
            ],
        },
        {
            "key": "plan",
            "label": "Plan",
            "fields": [
                {
                    "key": "follow_up",
                    "label": "Follow up",
                    "kind": "text",
                    "ai_hint": "When the client returns, as agreed.",
                }
            ],
        },
    ],
    "inputs": [],
}


@dataclass
class _Chart:
    """What a draft prompt's chart block says, line by line as rendered."""

    problems: list[str] = field(default_factory=list)
    allergies: str | None = None
    medications: list[str] | None = None
    """``None`` when the block has no medication list; ``[]`` when it says none recorded."""
    history: dict[str, str] = field(default_factory=dict)
    """Chart history text by field key, as recorded, the substance baseline included."""


#: How a client line names what they take: "Client: I'm taking A, B and C."
_STATED_MEDICATIONS = ("Client: I'm taking ", "Client: I am taking ")

#: How a client line changes a history field: "Client: Update on work_school: laid off."
_STATED_UPDATE = "Client: Update on "

#: How a client line answers a substance screen with no change: "Client: No change in alcohol."
_NO_CHANGE = "Client: No change in "

#: How the backend renders one chart-history field: ``  - key (Label, recorded date): text``.
_HISTORY_LINE = re.compile(r"^  - ([a-z_]+) \([^)]*, recorded [0-9-]+\): (.*)$")


def _chart(user_prompt: str) -> _Chart | None:
    """The chart block a draft prompt carries.

    ``None`` when the prompt has no chart (a preview, a meeting).
    """
    if "- Problem list:" not in user_prompt:
        return None
    chart = _Chart()
    listing: list[str] | None = None
    history_key: str | None = None
    in_history = False
    for line in user_prompt.splitlines():
        if in_history and (match := _HISTORY_LINE.match(line)):
            history_key = match.group(1)
            chart.history[history_key] = match.group(2)
            continue
        if history_key is not None and line.startswith("    "):
            chart.history[history_key] += "\n" + line.removeprefix("    ")
            continue
        history_key = None
        in_history = line == "- Chart history:" or line.startswith("- Substance use baseline")
        if line.startswith("- Problem list:"):
            rest = line.removeprefix("- Problem list:").strip()
            chart.problems.extend([rest] if rest else [])
            listing = None if rest else chart.problems
        elif line.startswith("- Current medications:"):
            chart.medications = []
            listing = chart.medications
        elif listing is not None and line.startswith("  "):
            # A medication group heading ("Psychiatric:") is part of the list as written.
            listing.append(line.strip().removeprefix("- "))
        else:
            listing = None
            if line.startswith("- Allergies: "):
                chart.allergies = line.removeprefix("- Allergies: ")
    return chart


#: How a field's hint says the draft prints it from the chart.
_FROM_THE_CHART = "From the chart"


def _chart_fed_fields(user_prompt: str) -> set[str]:
    """The fields the prompt says are printed from the chart (``* key (kind) — From the chart``).

    A model writes "Not recorded" in such a field when the chart has nothing
    for it, rather than drafting it from the visit. String operations only:
    the prompt is caller text, so no regex runs over it.
    """
    fields: set[str] = set()
    for line in user_prompt.splitlines():
        head, dash, hint = line.partition(" — ")
        if dash and hint.startswith(_FROM_THE_CHART) and head.lstrip().startswith("* "):
            fields.add(head.lstrip()[2:].partition(" ")[0])
    return fields


def _stated_updates(user_prompt: str) -> dict[str, str]:
    """What client lines say changed, by history key. String operations only: the
    prompt is caller text, so no regex runs over it."""
    updates: dict[str, str] = {}
    for line in user_prompt.splitlines():
        _, found, rest = line.partition(_STATED_UPDATE)
        key, sep, text = rest.partition(": ")
        if found and sep and text.strip():
            updates[key.strip()] = text.strip()
    return updates


def _unchanged(user_prompt: str) -> set[str]:
    """The substance keys a client line says did not change."""
    keys = set()
    for line in user_prompt.splitlines():
        _, found, rest = line.partition(_NO_CHANGE)
        if found:
            keys.add(rest.strip().removesuffix("."))
    return keys


def _screened(chart: _Chart, key: str, updates: dict[str, str], unchanged: set[str]) -> str:
    """A substance field: the chart's baseline, then this visit's screen."""
    if key in updates:
        screen = f'(stated this visit: "{updates[key]}")'
    elif key in unchanged:
        screen = "(asked this visit: no change)"
    else:
        screen = "(not asked this visit)"
    return f"{chart.history.get(key, 'Not recorded')} {screen}"


def _reported_medications(text: str) -> list[str]:
    """What ``I'm taking a, b and c`` lines name, in order. Plain string splitting:
    the prompt is caller text, so no regex runs over it."""
    stated: list[str] = []
    for line in text.splitlines():
        for opener in _STATED_MEDICATIONS:
            _, found, named = line.partition(opener)
            if found:
                named = named.strip().removesuffix(".").replace(" and ", ",")
                stated.extend(item.strip() for item in named.split(",") if item.strip())
    return stated


def _current_medications(chart_lines: list[str], user_prompt: str) -> list[str]:
    """The chart's list as written, then what the client says they take that it lacks.

    A stated medication is matched to the chart by its first word, the drug's
    name, and is added as ``(stated this visit: "...")`` only when the chart does
    not list it.
    """
    listed = chart_lines or ["None recorded"]
    on_chart = {line.split()[0].lower() for line in chart_lines if not line.endswith(":")}
    stated = _reported_medications(user_prompt)
    return listed + [
        f'(stated this visit: "{item}")'
        for item in stated
        if item.split()[0].lower() not in on_chart
    ]


def _with_chart(content: dict[str, Any], chart: _Chart, user_prompt: str = "") -> dict[str, Any]:
    """Echo the chart into the fields a model would put it in.

    A diagnosis field (or SOAP's clinical impression) names the listed
    problems; an allergies field states the chart's allergies; a current
    medications field is the chart's list, line for line, or "None recorded",
    then any medication a client line says they take that the chart lacks;
    a history field is the chart's text for its key, word for word ("Not
    recorded" where the chart has none and the hint says it comes from the
    chart; a chart-fed allergies field is the chart's allergies), then what
    a client line says changed; a substance field is the chart's baseline,
    then the visit's screen. So a spec can see that a draft was written
    against the chart it was handed.
    """
    updates = _stated_updates(user_prompt)
    unchanged = _unchanged(user_prompt)
    chart_fed = _chart_fed_fields(user_prompt)
    for section_key, section in content.items():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if key == "current_medications" and chart.medications is not None:
                section[key] = _current_medications(chart.medications, user_prompt)
            if not isinstance(value, str):
                continue
            if section_key == "substance_use":
                section[key] = _screened(chart, key, updates, unchanged)
                continue
            if key in chart.history or key in updates or key in chart_fed:
                # A chart-fed allergies field reads the chart's allergy list,
                # which is not a history key.
                on_chart = chart.allergies if "allerg" in key else None
                section[key] = chart.history.get(key, on_chart or "Not recorded")
                if key in updates:
                    section[key] += f' (stated this visit: "{updates[key]}")'
                continue
            if "diagnos" in key or key == "clinical_impression":
                section[key] = f"{value} Problem list: {'; '.join(chart.problems)}."
            elif "allerg" in key and chart.allergies is not None:
                section[key] = f"{value} Allergies: {chart.allergies}."
    return content


def _source_note(user_prompt: str) -> str | None:
    """The note an extraction call quotes, or None for any other call."""
    if not user_prompt.startswith("# Source note"):
        return None
    _, _, rest = user_prompt.partition('"""\n')
    body, _, _ = rest.partition('\n"""')
    return body


def _slug(text: str) -> str:
    return "_".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def _extracted(schema: dict[str, Any], note: str) -> dict[str, Any]:
    """Relocate ``Label: text`` lines into the field whose key is the label.

    The verbatim relocation a model does, for the one layout specs write;
    a line with no such field lands nowhere, which is what a coverage
    check looks for.
    """
    fields: dict[str, tuple[str, str, bool]] = {}
    content: dict[str, Any] = {}
    for section, sub in schema.get("properties", {}).items():
        content[section] = {}
        for key, spec in sub.get("properties", {}).items():
            is_list = spec.get("type") == "array"
            content[section][key] = [] if is_list else ""
            fields[key] = (section, key, is_list)
    for line in note.splitlines():
        label, sep, text = line.partition(":")
        target = fields.get(_slug(label)) if sep else None
        if target is None or not text.strip():
            continue
        section, key, is_list = target
        content[section][key] = [text.strip()] if is_list else text.strip()
    return content


@app.post("/notes/v1/structured")
async def draft_note(call: NoteCall) -> dict[str, Any]:
    """Note drafts: NOTE_GENERATION_BASE_URL points at ``/notes``.

    Also the note-type derive calls: the proposal (its schema is titled
    with the spec it must validate as) and each sample's extraction into it.
    """
    if REFUSES_DRAFT in call.user_prompt:
        raise HTTPException(status_code=422, detail="draft refused")
    if PRIMARY_DOWN in call.user_prompt and call.model != FALLBACK_MODEL:
        raise HTTPException(status_code=503, detail="model unavailable")
    titled = _TITLED_CALLS.get(call.response_schema.get("title") or "")
    if titled is not None:
        return {"data": titled(call), "finish_reason": "stop"}
    if "runs" in call.response_schema.get("properties", {}):
        return {"data": _turn_labels(call.user_prompt), "finish_reason": "stop"}
    note = _source_note(call.user_prompt)
    if note is not None:
        return {"data": _extracted(call.response_schema, note), "finish_reason": "stop"}
    draft = _stand_in(call.response_schema, "")
    _fill_named(draft, _supplied_inputs(call.user_prompt))
    _fill_named(draft, _dictated(call.user_prompt))
    chart = _chart(call.user_prompt)
    if chart is not None:
        draft = _with_chart(draft, chart, call.user_prompt)
    if "psychotherapy_time_stated" in call.response_schema.get("properties", {}):
        draft["psychotherapy_time_stated"] = _stated_time(call.user_prompt)
    return {"data": draft, "finish_reason": "stop"}


#: How the backend heads what a clinician dictated after the session
#: (app.services.note_redraft.DICTATED_HEADING).
DICTATED_HEADING = "Dictated by the clinician after the session"

#: How the backend heads the note a redraft starts from
#: (app.services.note_generation_service._CURRENT_NOTE_INSTRUCTIONS).
CURRENT_NOTE_HEADING = "\n\nCurrent note:"

#: Every dictated clip transcribes to this. Its line is relocated into the
#: field it names, so a redraft with it visibly gains it.
DICTATION_TEXT = "Next session: Two weeks from today, same time."


def _dictated(user_prompt: str) -> dict[str, str]:
    """``Label: text`` lines dictated after the session, by label slug."""
    # A redraft's prompt ends with the note as it stands, which is not dictation.
    before_note = user_prompt.partition(CURRENT_NOTE_HEADING)[0]
    _, found, rest = before_note.partition(DICTATED_HEADING)
    values: dict[str, str] = {}
    if not found:
        return values
    for line in rest.splitlines()[1:]:
        # A prompt may number transcript lines ("[S4] ..."); the label follows.
        label, sep, text = re.sub(r"^(\[[^\]]*\]\s*)+", "", line).partition(":")
        if sep and text.strip():
            values[_slug(label)] = text.strip()
    return values


#: How a client line says where they are for a telehealth visit: "Client: I'm at home today."
_AT_HOME = "Client: I'm at home"


def _numbered(line: str) -> tuple[int | None, str]:
    """A ``[Sn] ...`` line's number and the rest of it; ``None`` for any other line."""
    head, sep, rest = line.partition("] ")
    number = head.removeprefix("[S")
    return (int(number), rest) if sep and head.startswith("[S") and number.isdigit() else (None, "")


def _chart_line(user_prompt: str, key: str) -> str:
    """The chart's text the extraction prompt shows for ``key``: ``- key (Label): text``."""
    for line in user_prompt.splitlines():
        if line.startswith(f"- {key} ("):
            return line.partition("): ")[2]
    return ""


def _chart_field_statements(schema: dict[str, Any], user_prompt: str) -> dict[str, Any]:
    """What the visit said about the chart-fed fields, from the same lines a draft reads.

    ``Update on <key>: <text>`` states ``text`` about that field; ``No change in
    <key>.`` is a substance screen with no change; ``I'm taking a, b`` names the
    medications the client takes, of which those the chart's list lacks (matched
    by the drug's name) are stated. Each cites its own line. Nothing is said
    about the allergies unless a line says it, so a spec can see the chart's
    allergies printed with no model involved.
    """
    properties = schema.get("properties", {})
    keys = set(properties["statements"]["items"]["properties"]["field_key"].get("enum", []))
    # A chart line may carry its category first ("Psychiatric: Sertraline 50 mg").
    on_chart = {
        entry.rpartition(": ")[2].split()[0].lower()
        for entry in _chart_line(user_prompt, "current_medications").split("; ")
        if entry and entry != "None recorded"
    }
    statements: list[dict[str, Any]] = []
    at_home: list[int] = []
    for line in user_prompt.splitlines():
        number, rest = _numbered(line)
        if number is None:
            continue
        evidence = [number]
        _, found, update = rest.partition(_STATED_UPDATE)
        key, sep, text = update.partition(": ")
        if found and sep and key.strip() in keys and text.strip():
            statements.append(
                {
                    "field_key": key.strip(),
                    "screen": "stated",
                    "stated": text.strip(),
                    "evidence_segment_ids": evidence,
                }
            )
        _, found, unchanged = rest.partition(_NO_CHANGE)
        if found and (key := unchanged.strip().removesuffix(".")) in keys:
            statements.append(
                {
                    "field_key": key,
                    "screen": "asked_no_change",
                    "stated": "",
                    "evidence_segment_ids": evidence,
                }
            )
        if "current_medications" in keys:
            statements.extend(
                {
                    "field_key": "current_medications",
                    "screen": "stated",
                    "stated": item,
                    "evidence_segment_ids": evidence,
                }
                for item in _reported_medications(rest)
                if item.split()[0].lower() not in on_chart
            )
        if _AT_HOME in rest:
            at_home = evidence
    reply: dict[str, Any] = {"statements": statements, "risk_and_safety_plan_segment_ids": []}
    if "diagnoses" in properties:
        # As a draft's diagnoses field always held one: a coded stand-in, named
        # on the visit's first line, so signing offers it to the problem list.
        first = next(
            (n for n, _ in map(_numbered, user_prompt.splitlines()) if n is not None), None
        )
        reply["diagnoses"] = (
            [
                {
                    "label": "Stand-in diagnosis for assessment.diagnoses",
                    "code": "F00.0",
                    "evidence_segment_ids": [first],
                }
            ]
            if first is not None
            else []
        )
    if "client_at_home" in properties:
        reply["client_at_home"] = {"at_home": bool(at_home), "evidence_segment_ids": at_home}
    return reply


def _risk_sections(schema: dict[str, Any], user_prompt: str) -> dict[str, Any]:
    """The risk, mental status and measures call, answered as a draft's fields always were.

    Each field reads "Stand-in draft for <section>.<field>.", or what was dictated
    under its name after the session. A risk field's text comes back with no
    quotations of the client, which a model may also return.
    """
    reply: dict[str, Any] = {}
    dictated = _dictated(user_prompt)
    for section, sub in schema.get("properties", {}).items():
        reply[section] = {}
        for key, spec in sub.get("properties", {}).items():
            text = dictated.get(key, f"Stand-in draft for {section}.{key}.")
            if spec.get("type") == "object":
                reply[section][key] = {"text": text, "quotes": []}
            elif spec.get("type") == "array":
                reply[section][key] = [text]
            else:
                reply[section][key] = text
    return reply


def _proposals_reply(call: NoteCall) -> dict[str, Any]:
    return {
        "proposals": _chart_proposals(call.user_prompt),
        "medication_changes": _medication_changes(call.user_prompt),
    }


#: The calls a schema's title names, each answered in its own shape.
_TITLED_CALLS: dict[str, Callable[[NoteCall], dict[str, Any]]] = {
    "PracticeNoteTypeSpec": lambda _call: DERIVED_PROPOSAL,
    "ChartFieldStatements": lambda call: _chart_field_statements(
        call.response_schema, call.user_prompt
    ),
    "RiskMentalStatusSections": lambda call: _risk_sections(call.response_schema, call.user_prompt),
    "ChartProposals": _proposals_reply,
}


def _chart_proposals(user_prompt: str) -> list[dict[str, Any]]:
    """One proposal per numbered "Update on <key>: <text>" client line, citing it.

    The same lines a draft marks "(stated this visit: ...)". The proposal
    call numbers each line ``[Sn]``; the proposal cites that line, as a model
    would cite the line that says it. For allergies the text is
    ``<substance> - <reaction>``.
    """
    proposals = []
    for line in user_prompt.splitlines():
        head, found, rest = line.partition(_STATED_UPDATE)
        if not found or not head.startswith("[S"):
            continue
        segment = head[2:].partition("]")[0]
        key, _, text = rest.partition(": ")
        entry, _, reaction = text.partition(" - ") if key == "allergies" else ("", "", text)
        proposals.append(
            {
                "field_key": key.strip(),
                "entry": entry.strip(),
                "proposed_text": reaction.strip(),
                "what_changed": "Stated this visit",
                "evidence_segment_ids": [int(segment)] if segment.isdigit() else [],
            }
        )
    return proposals


#: How a clinician line changes the medication list:
#: "Clinician: Medication start: hydroxyzine; 25 mg; in the afternoon as needed".
#: After the name come the dose, the frequency and, for a stop, the reason; any
#: may be left empty.
_MEDICATION_DECISION = "Clinician: Medication "


def _medication_changes(user_prompt: str) -> list[dict[str, Any]]:
    """One medication change per numbered "Medication <action>: ..." line, citing it."""
    changes = []
    for line in user_prompt.splitlines():
        head, found, rest = line.partition(_MEDICATION_DECISION)
        if not found or not head.startswith("[S"):
            continue
        segment = head[2:].partition("]")[0]
        action, _, values = rest.partition(": ")
        name, dose, frequency, reason = ([*values.split(";"), "", "", ""])[:4]
        change = {
            "action": action.strip(),
            "drug_name": name.strip(),
            "what_changed": "Stated this visit",
            "evidence_segment_ids": [int(segment)] if segment.isdigit() else [],
        }
        given = {"dose": dose, "frequency": frequency, "reason": reason}
        change.update({k: v.strip() for k, v in given.items() if v.strip()})
        changes.append(change)
    return changes


@app.post("/transcription/v1/transcribe")
async def transcribe() -> dict[str, str]:
    """Dictated clips: DICTATION_TRANSCRIPTION_BASE_URL points at ``/transcription``."""
    return {"text": DICTATION_TEXT}


def _supplied_inputs(user_prompt: str) -> dict[str, str]:
    """The note type's inputs as the default prompt lists them, by label slug.

    ``Inputs:`` then one ``- Label: value`` line each, up to a blank line;
    ``not provided`` is the prompt's word for none.
    """
    values: dict[str, str] = {}
    _, found, rest = user_prompt.partition("\nInputs:\n")
    if not found:
        return values
    for line in rest.split("\n\n", 1)[0].splitlines():
        label, sep, value = line.removeprefix("- ").partition(":")
        if sep and value.strip() and value.strip() != "not provided":
            values[_slug(label)] = value.strip()
    return values


def _fill_named(draft: dict[str, Any], values: dict[str, str]) -> None:
    """Write each value into the text field whose key it is named after.

    So a spec can see what reached the prompt: a type with an input and a
    field of the same name drafts that field as the input's value.
    """
    for section in draft.values():
        if not isinstance(section, dict):
            continue
        for key, current in section.items():
            if key in values and isinstance(current, str):
                section[key] = values[key]


#: Minutes of psychotherapy the clinician dictated, as the stand-in hears them.
STATED_MINUTES = re.compile(r"Psychotherapy (\d+) minutes", re.IGNORECASE)


def _stated_time(user_prompt: str) -> dict[str, Any]:
    """The psychotherapy time the clinician dictated: only minutes, when said."""
    said = STATED_MINUTES.search(user_prompt)
    if said is None:
        return {"start": "", "end": "", "as_dictated": ""}
    return {"start": "", "end": "", "minutes": int(said.group(1)), "as_dictated": said.group(0)}


#: The turns a labeling call numbers ("[S3] Therapist: ...").
LABELED_TURN = re.compile(r"^\[S(\d+)\] (\w+): (.*)$", re.MULTILINE)

#: What the stand-in reads a turn as, by its words; the first match wins.
TURN_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"medication|dose|side effect", re.IGNORECASE), "medication_management"),
    (re.compile(r"hurting yourself|better off dead", re.IGNORECASE), "screening_risk"),
    (re.compile(r"next month|see you", re.IGNORECASE), "admin"),
)
THERAPY_CUE = "let's get into"


def _turn_labels(user_prompt: str) -> dict[str, Any]:
    """Every turn labeled, one run each.

    A clinician's turn is read by its words; before the spoken cue ("let's
    get into") it is admin, from the cue on therapy. A client's turn answers
    the turn before it and takes its label unless its own words say otherwise.
    """
    runs: list[dict[str, Any]] = []
    cue = -1
    label = "admin"
    for match in LABELED_TURN.finditer(user_prompt):
        index, speaker, text = int(match.group(1)), match.group(2), match.group(3)
        if THERAPY_CUE in text.lower():
            cue = index
        said = next((name for words, name in TURN_WORDS if words.search(text)), None)
        if said is not None:
            label = said
        elif speaker != "Client":
            label = "therapy" if cue >= 0 else "admin"
        runs.append({"first_segment": index, "last_segment": index, "label": label})
    return {"runs": runs, "cue_segment": cue}


@app.get("/_fake/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
