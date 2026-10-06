# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Propose where a drafted visit's psychotherapy portion began.

Two of the three signals in :mod:`app.notes.visit_times` come from the draft:

- the model marks the turn where the therapy portion begins (and says
  whether the clinician cued it aloud), in the same call that drafts the
  note — :data:`START_SCHEMA` and :data:`START_INSTRUCTIONS`;
- a second, small call attributes the drafted interventions to transcript
  turns, the same way a SOAP draft's sentences are linked to their sources.

Neither is fatal: a draft whose start cannot be read keeps its content and
offers no proposal, and the clinician types the minutes or picks the turn.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from ..models import SOAPSentence
from ..notes.visit_times import (
    PSYCHOTHERAPY_SECTION_KEY,
    parse_transcript_time,
    resolve_start_candidates,
    snap_to_turn,
    stated_clock_time,
)
from .source_attribution_service import build_attribution_prompt, parse_attribution_response

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from ..notes.client_present import TimedSegment

logger = logging.getLogger(__name__)

START_KEY = "psychotherapy_start"

START_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "transcript_time": {"type": "string"},
        "cued_by_clinician": {"type": "boolean"},
        "stated_clock_time": {"type": "string"},
    },
}

START_INSTRUCTIONS = (
    f"Also return {START_KEY}, which is not part of the note:\n"
    "- transcript_time: the timestamp, exactly as written in the transcript, "
    "of the turn where the psychotherapy portion of the visit begins, after "
    "any medication-management portion. Empty if no psychotherapy took place.\n"
    "- cued_by_clinician: true only if, at that turn, the clinician said aloud "
    "that the therapy portion was starting (for example \"let's get into the "
    'session work").\n'
    "- stated_clock_time: a start time for the therapy that the clinician "
    'stated (for example "therapy started around 10:15"), exactly as said. '
    "Empty if none was stated."
)

_INTERVENTION_FIELD = "intervention"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _intervention_sentences(content: dict[str, Any]) -> list[str]:
    section = content.get(PSYCHOTHERAPY_SECTION_KEY) or {}
    sentences: list[str] = []
    for key, value in section.items():
        if _INTERVENTION_FIELD in key and isinstance(value, str):
            sentences.extend(s for s in _SENTENCE_END.split(value.strip()) if s)
    return [s for s in sentences if s.rstrip(".").lower() != "not stated"]


def attributed_start(
    content: dict[str, Any],
    turns: Sequence[TimedSegment],
    complete: Callable[[str], dict[str, Any]],
) -> float | None:
    """The earliest turn the drafted interventions are attributed to.

    ``complete`` runs one structured attribution call on a prompt and returns
    its parsed reply.
    """
    sentences = _intervention_sentences(content)
    if not sentences or not turns:
        return None
    claims = {
        f"{PSYCHOTHERAPY_SECTION_KEY}.interventions.{i}": SOAPSentence(text=text)
        for i, text in enumerate(sentences)
    }
    indexed = "\n".join(f"[S{i}] {t.speaker}: {t.text}" for i, t in enumerate(turns))
    try:
        reply = complete(build_attribution_prompt(claims, indexed))
        parse_attribution_response(json.dumps(reply), claims, max_segment_id=len(turns) - 1)
    except Exception:
        logger.warning("Psychotherapy start attribution failed; no attributed start", exc_info=True)
        return None
    cited = [i for claim in claims.values() for i in claim.source_segment_ids]
    return min((turns[i].start for i in cited), default=None)


def propose_start(
    mark: dict[str, Any] | None,
    attributed: float | None,
    turns: Sequence[TimedSegment],
) -> dict[str, Any] | None:
    """The stored proposal: candidate starts and any start time the clinician stated."""
    mark = mark if isinstance(mark, dict) else {}
    marked_time = parse_transcript_time(str(mark.get("transcript_time") or ""))
    marked = snap_to_turn(marked_time, turns) if marked_time is not None else None
    candidates = resolve_start_candidates(
        marked=marked, cued=bool(mark.get("cued_by_clinician")), attributed=attributed
    )
    stated = stated_clock_time(str(mark.get("stated_clock_time") or ""))
    if not candidates and stated is None:
        return None
    return {
        "candidates": [{"seconds": c.seconds, "source": c.source} for c in candidates],
        "stated_clock_time": stated,
    }


__all__ = [
    "START_INSTRUCTIONS",
    "START_KEY",
    "START_SCHEMA",
    "attributed_start",
    "propose_start",
]
