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
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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
    """Chart history text by field key, as recorded (the substance baseline excluded)."""


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
        in_history = line == "- Chart history:"
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


def _with_chart(content: dict[str, Any], chart: _Chart) -> dict[str, Any]:
    """Echo the chart into the fields a model would put it in.

    A diagnosis field (or SOAP's clinical impression) names the listed
    problems; an allergies field states the chart's allergies; a current
    medications field is the chart's list, line for line, or "None recorded";
    a history field is the chart's text for its key, word for word. So a spec
    can see that a draft was written against the chart it was handed.
    """
    for section_key, section in content.items():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if key == "current_medications" and chart.medications is not None:
                section[key] = chart.medications or ["None recorded"]
            if not isinstance(value, str):
                continue
            if key in chart.history and section_key != "substance_use":
                section[key] = chart.history[key]
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
    if call.response_schema.get("title") == "PracticeNoteTypeSpec":
        return {"data": DERIVED_PROPOSAL, "finish_reason": "stop"}
    note = _source_note(call.user_prompt)
    if note is not None:
        return {"data": _extracted(call.response_schema, note), "finish_reason": "stop"}
    draft = _stand_in(call.response_schema, "")
    _fill_named(draft, _supplied_inputs(call.user_prompt))
    _fill_named(draft, _dictated(call.user_prompt))
    chart = _chart(call.user_prompt)
    if chart is not None:
        draft = _with_chart(draft, chart)
    if "psychotherapy_start" in call.response_schema.get("properties", {}):
        draft["psychotherapy_start"] = _therapy_start(call.user_prompt)
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


#: The clinician's spoken cue the stand-in recognizes as the therapy portion starting.
THERAPY_CUE = re.compile(
    r"^\[(\d+(?::\d{2}){1,2})\][^\n]*let's get into", re.IGNORECASE | re.MULTILINE
)


def _therapy_start(user_prompt: str) -> dict[str, Any]:
    """Where the therapy portion began: the turn with the clinician's cue, if any."""
    cue = THERAPY_CUE.search(user_prompt)
    return {
        "transcript_time": cue.group(1) if cue else "",
        "cued_by_clinician": cue is not None,
        "stated_clock_time": "",
    }


@app.get("/_fake/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
