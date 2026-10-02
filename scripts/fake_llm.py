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

from typing import Any

from fastapi import FastAPI
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


class StructuredCall(BaseModel):
    """What the backend sends; only the sentence decides the reply."""

    model: str = ""
    system_prompt: str = ""
    user_prompt: str


def _key(sentence: str) -> str:
    return " ".join(sentence.lower().split()).rstrip(".")


@app.post("/v1/structured")
def structured(call: StructuredCall) -> dict[str, Any]:
    return {"data": READINGS.get(_key(call.user_prompt), UNKNOWN), "finish_reason": "stop"}


@app.get("/_fake/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
