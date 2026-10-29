# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Between-visit tasks in a note's prose plan, each with the words it came from.

A psychiatric note's plan is a paragraph or two that mixes what the
prescriber will do (start, continue or taper a medication, order labs, see
the client again in four weeks) with, sometimes, what the client agreed to
do in between (keep a sleep diary, practise the breathing exercise). Only
the second kind is homework, and telling them apart is reading, so one
structured call does it.

Three rules bound what the call can put in front of a clinician:

* **Quoted or dropped.** Every task comes with the note's own words it was
  read from, and a task whose quote is not in the note is discarded here.
  The call can therefore only ever restate what the clinician signed.
* **Suggested, never assigned.** What this returns is a suggestion; the
  clinician accepts, edits or discards each one at signing before a client
  sees it.
* **Only the plan fields.** The input is the text of the fields
  :mod:`app.homework.sources` names, never the transcript. A psychotherapy
  note has none, so it is never read.

Logs counts and timings only.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..services.ai_features import AIFeature
from ..services.hedged_structured_llm_gateway import HedgedStructuredLLMGateway
from ..settings import get_settings
from .sources import MAX_TASK_CHARS

if TYPE_CHECKING:
    from ..chart_proposals.drafting import CompleteStructured
    from ..services.structured_llm_gateway import StructuredLLMGateway

logger = logging.getLogger(__name__)

#: At most this many proposals from one note: a plan is not a workbook.
MAX_PROPOSALS = 6

_MAX_OUTPUT_TOKENS = 1_024
_ATTEMPT_TIMEOUT_SECONDS = 20.0

SYSTEM_PROMPT = """\
You read the plan of a signed clinical note and list the tasks the client \
agreed to do, or was asked to do, on their own before the next visit: \
practice of a skill, a log or diary, a worksheet, reading, an exposure or \
activity to try.

Leave out everything else, even when it is in the plan:
- medication instructions of any kind (start, continue, stop, taper, take, \
increase, decrease, a dose or a time to take it);
- labs, tests, monitoring and vitals;
- appointments, follow-up timing, therapy frequency and referrals;
- anything the clinician will do;
- education or counselling given during the visit, unless it ends in a task \
the client will do;
- crisis, safety-plan and emergency instructions.

For each task give:
- "quote": the exact words of the note the task comes from, copied \
character for character, without changing or adding anything;
- "text": the task as a short instruction to the client in the second \
person ("Keep a sleep diary each night"), using only what the note says. \
Do not add a frequency, an amount, a reason or advice the note does not \
state.

If the plan has no such task, return an empty list. Never invent a task."""

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["text", "quote"],
            },
        }
    },
    "required": ["assignments"],
}


@dataclass(frozen=True)
class PlanField:
    """One prose field handed to the call: its key, its label, its text."""

    key: str
    label: str
    text: str


@dataclass(frozen=True)
class DraftedTask:
    """A task read from the plan, the field it was in, and the note's words."""

    source_field: str
    quote: str
    text: str


_SPACE = re.compile(r"\s+")
_QUOTE_MARKS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'})


def _normalise(text: str) -> str:
    return _SPACE.sub(" ", text.translate(_QUOTE_MARKS)).strip().lower()


def user_prompt(fields: list[PlanField]) -> str:
    """The plan as the call reads it: one labelled block per field."""
    return "\n\n".join(f"{f.label}:\n{f.text}" for f in fields)


def grounded(raw: Any, fields: list[PlanField]) -> list[DraftedTask]:
    """The tasks whose quote is in the plan, each tied to the field it is in.

    Anything malformed, unquoted, too long or repeated is dropped. Order is
    the call's, capped at :data:`MAX_PROPOSALS`.
    """
    items = raw.get("assignments") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    haystacks = [(f.key, _normalise(f.text)) for f in fields]
    out: list[DraftedTask] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        quote = str(item.get("quote") or "").strip()
        needle = _normalise(quote)
        if not text or not needle or len(text) > MAX_TASK_CHARS:
            continue
        field_key = next((key for key, hay in haystacks if needle in hay), None)
        if field_key is None or _normalise(text) in seen:
            continue
        seen.add(_normalise(text))
        out.append(DraftedTask(field_key, quote, text[:1].upper() + text[1:]))
        if len(out) == MAX_PROPOSALS:
            break
    return out


class HomeworkDrafter:
    """Reads a prose plan for between-visit tasks. One structured call per note.

    In the app the call is the draft's own (``chart_proposal_completion``: the
    same model and provider that wrote the note, beside the chart-update
    call), so a deployment's note model is what reads the plan. Without one,
    a gateway and model are resolved the way note generation resolves them,
    which is how the eval runs it; ``--model`` there grades one model alone.
    """

    def __init__(
        self,
        complete: CompleteStructured | None = None,
        *,
        llm_gateway: StructuredLLMGateway | None = None,
        model: str | None = None,
    ) -> None:
        self._complete = complete
        self._llm_gateway = llm_gateway
        self._model = model

    def _call(self, fields: list[PlanField]) -> dict[str, Any]:
        if self._complete is not None:
            return self._complete(SYSTEM_PROMPT, user_prompt(fields), RESPONSE_SCHEMA)
        settings = get_settings()
        gateway = self._llm_gateway or HedgedStructuredLLMGateway.from_settings(
            feature=AIFeature.NOTE_GENERATION
        )
        model = self._model or settings.model_for(AIFeature.NOTE_GENERATION, settings.ai_model)
        return gateway.complete_structured(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt(fields),
            response_schema=RESPONSE_SCHEMA,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.0,
            thinking_budget=0,
            timeout_seconds=_ATTEMPT_TIMEOUT_SECONDS,
        ).data

    def draft(self, fields: list[PlanField]) -> list[DraftedTask]:
        """The grounded tasks in ``fields``. Raises when the call fails."""
        return self.read(fields)[0]

    def read(self, fields: list[PlanField]) -> tuple[list[DraftedTask], int]:
        """The grounded tasks, and how many the call returned before grounding.

        The count is for the eval, which reports how often a model returns a
        task it cannot quote; :meth:`draft` is what the app calls.
        """
        if not fields:
            return [], 0
        started = time.monotonic()
        data = self._call(fields)
        tasks = grounded(data, fields)
        raw = data.get("assignments") if isinstance(data, dict) else None
        returned = len(raw) if isinstance(raw, list) else 0
        logger.info(
            "Homework read: %d field(s), %d returned, %d kept, latency_ms=%d",
            len(fields),
            returned,
            len(tasks),
            int((time.monotonic() - started) * 1000),
        )
        return tasks, returned
