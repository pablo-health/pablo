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
from collections import Counter
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


class NoteCall(BaseModel):
    """A note draft request; the response schema decides the reply's shape."""

    response_schema: dict[str, Any]
    user_prompt: str = ""


def _stand_in(schema: dict[str, Any], path: str) -> Any:
    """A value of the schema's shape, the same for the same schema every run.

    Text says which field it fills, so a spec can find it; a list of text
    holds one such entry; a list of objects (the sentence-to-transcript
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
        return [] if items.get("type") == "object" else [_stand_in(items, path)]
    if kind in ("integer", "number"):
        return 0
    if kind == "boolean":
        return False
    return f"Stand-in draft for {path}."


@app.post("/notes/v1/structured")
async def draft_note(call: NoteCall) -> dict[str, Any]:
    """Note drafts: NOTE_GENERATION_BASE_URL points at ``/notes``."""
    if REFUSES_DRAFT in call.user_prompt:
        raise HTTPException(status_code=422, detail="draft refused")
    return {"data": _stand_in(call.response_schema, ""), "finish_reason": "stop"}


@app.get("/_fake/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
