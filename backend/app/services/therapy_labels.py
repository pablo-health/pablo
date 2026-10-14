# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Propose a drafted visit's psychotherapy time: what was dictated, and which turns were therapy.

Two parts, both structured, neither read out of free text:

- the call that drafts the note also returns the psychotherapy time the
  clinician stated, part by part (:data:`TIME_SCHEMA`, :data:`TIME_INSTRUCTIONS`);
- a second, small call labels every client-present turn as therapy,
  medication management, screening and risk, or admin (:func:`label_turns`).
  Where the psychotherapy block is drafted by a call of its own, the labels
  come first, beside the draft, and decide whether that call runs; otherwise
  they follow the draft and read its psychotherapy section as context. Where
  the clinician said aloud that the therapy was starting, that cue informs
  the labels and is returned with them.

Neither is fatal: a reply that cannot be read keeps the draft and proposes
nothing, and the clinician labels the turns or types the minutes.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from ..notes.visit_times import (
    PSYCHOTHERAPY_SECTION_KEY,
    PSYCHOTHERAPY_TIME_FIELD,
    TURN_LABELS,
    DictatedTime,
    labels_to_stored,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ..notes.client_present import TimedSegment
    from ..notes.visit_times import TurnLabel

logger = logging.getLogger(__name__)

TIME_KEY = "psychotherapy_time_stated"

TIME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "start": {"type": "string"},
        "end": {"type": "string"},
        "minutes": {"type": "integer"},
        "as_dictated": {"type": "string"},
    },
}

TIME_INSTRUCTIONS = (
    f"Also return {TIME_KEY}, which is not part of the note: the psychotherapy "
    "time the clinician stated aloud, part by part, never computed or estimated "
    "from the transcript timestamps.\n"
    '- start: the start time of the therapy as the clinician said it (for example "10:14"). '
    "Empty if not stated.\n"
    "- end: the end time as the clinician said it. Empty if not stated.\n"
    "- minutes: the number of psychotherapy minutes the clinician said. Leave it "
    "out if the clinician did not say a number of minutes.\n"
    "- as_dictated: the clinician's words that state the time, verbatim. Empty if "
    "none were said."
)

LABEL_SYSTEM_PROMPT = (
    "You label the turns of a recorded visit that paired a medication check with "
    "psychotherapy. Every turn gets exactly one label. Return ONLY a JSON object."
)

LABEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "runs": {
            "type": "array",
            "items": {
                "type": "object",
                "title": "LabeledRun",
                "properties": {
                    "first_segment": {"type": "integer"},
                    "last_segment": {"type": "integer"},
                    "label": {"type": "string", "enum": list(TURN_LABELS)},
                },
                "required": ["first_segment", "last_segment", "label"],
            },
        },
        "cue_segment": {"type": "integer"},
    },
    "required": ["runs"],
}

_LABEL_GUIDE = """\
Labels:
- therapy: psychotherapy work: exploring thoughts, feelings and behavior, skills \
practice, exposure, cognitive restructuring, processing, homework review and assignment.
- medication_management: medications, doses, adherence, side effects, labs, \
prescriptions and refills, and the clinician's medication decisions.
- screening_risk: symptom screening, rating scales, substance use questions, and \
questions about suicidal or homicidal thoughts, self-harm or safety.
- admin: greetings, location and consent, scheduling, goodbyes and anything else.

The portions may alternate: therapy, then medication questions, then therapy again. \
Label each turn by what that part of the conversation is doing, not by where it falls \
in the visit. If the clinician said aloud that the therapy portion was starting (for \
example "let's get into the session work"), return that turn as cue_segment; the \
turns that follow are therapy until the conversation turns to something else. \
Return -1 as cue_segment if there was no such cue.

Return runs of consecutive turns with one label each: first_segment and last_segment \
are the numbers after S in [Sn], inclusive. Cover every turn from S0 to the last."""


def stated_time(reply: Any) -> DictatedTime | None:
    """The dictated time the draft returned, if the clinician stated one."""
    return DictatedTime.from_reply(reply)


def _draft_context(content: dict[str, Any]) -> str:
    """The drafted psychotherapy section, which names the therapy that took place."""
    section = dict(content.get(PSYCHOTHERAPY_SECTION_KEY) or {})
    section.pop(PSYCHOTHERAPY_TIME_FIELD, None)
    return json.dumps(section, indent=2, ensure_ascii=False)


def build_label_prompt(content: dict[str, Any] | None, turns: Sequence[TimedSegment]) -> str:
    """The labeling prompt; with the drafted psychotherapy section as context when
    there is a draft, and without it when the labels come first (they decide
    whether the psychotherapy block is drafted at all)."""
    indexed = "\n".join(f"[S{i}] {t.speaker}: {t.text}" for i, t in enumerate(turns))
    drafted = (
        "The psychotherapy section of the note drafted from this visit:\n"
        f"{_draft_context(content)}\n\n"
        if content is not None
        else ""
    )
    return f"{_LABEL_GUIDE}\n\n{drafted}Transcript, while the client was present:\n{indexed}"


def parse_labels(
    reply: dict[str, Any], turns: Sequence[TimedSegment]
) -> tuple[dict[float, TurnLabel], float | None]:
    """Per-turn labels keyed by the turn's start, and the cued turn's start.

    A run outside the transcript is ignored; where two runs claim a turn,
    the first one wins; a turn no run covers stays unattributed.
    """
    labels: dict[float, TurnLabel] = {}
    for run in reply.get("runs") or []:
        if not isinstance(run, dict) or run.get("label") not in TURN_LABELS:
            continue
        first, last = run.get("first_segment"), run.get("last_segment")
        if not isinstance(first, int) or not isinstance(last, int):
            continue
        for i in range(max(first, 0), min(last, len(turns) - 1) + 1):
            labels.setdefault(turns[i].start, run["label"])
    cue = reply.get("cue_segment")
    cued = turns[cue].start if isinstance(cue, int) and 0 <= cue < len(turns) else None
    return labels, cued


def label_turns(
    content: dict[str, Any] | None,
    turns: Sequence[TimedSegment],
    complete: Callable[[str], dict[str, Any]],
) -> tuple[dict[float, TurnLabel], float | None]:
    """Label every client-present turn; nothing on a failed or unreadable call.

    ``complete`` runs one structured labeling call on a prompt and returns
    its parsed reply.
    """
    if not turns:
        return {}, None
    try:
        return parse_labels(complete(build_label_prompt(content, turns)), turns)
    except Exception:
        logger.warning("Psychotherapy turn labeling failed; no labels proposed", exc_info=True)
        return {}, None


def propose(
    dictated: DictatedTime | None,
    labels: dict[float, TurnLabel],
    cue_seconds: float | None,
) -> dict[str, Any] | None:
    """The stored proposal: the dictated time, the turn labels, the spoken cue."""
    if dictated is None and not labels:
        return None
    return {
        "dictated": dictated.to_dict() if dictated else None,
        "labels": labels_to_stored(labels),
        "cue_seconds": cue_seconds,
    }


__all__ = [
    "LABEL_SCHEMA",
    "LABEL_SYSTEM_PROMPT",
    "TIME_INSTRUCTIONS",
    "TIME_KEY",
    "TIME_SCHEMA",
    "build_label_prompt",
    "label_turns",
    "parse_labels",
    "propose",
    "stated_time",
]
