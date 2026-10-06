# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The psychotherapy window of a visit: where it starts, how long it ran.

A visit that pairs a medication check with psychotherapy bills the therapy
portion by its face-to-face minutes, so those minutes are the therapy
portion only, with the client present: from where the therapy began to
where the client left (:mod:`app.notes.client_present`). The whole
client-present span is never the answer when a medication portion came
first.

The start is proposed from the transcript and confirmed by the clinician:

- the clinician's own spoken cue ("let's get into the session work") wins;
- otherwise the turn the draft marks as the start of the therapy portion,
  and the earliest turn the drafted interventions are attributed to. When
  those two disagree by more than :data:`SIGNALS_AGREE_SECONDS`, both are
  offered and the clinician picks.

A start time the clinician said aloud ("therapy started around 10:15") is
kept as said; placing a clock time on the recording needs the clinician's
time zone, so the page does it.

Once confirmed, the window is written into the note's psychotherapy time
field as clock times and minutes. A time the clinician dictated wins: if the
draft already holds a dictated time that disagrees, both are shown and the
clinician picks.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, tzinfo

    from .client_present import TimedSegment

PSYCHOTHERAPY_SECTION_KEY = "psychotherapy"
"""The section a type carries for a visit's psychotherapy portion."""

PSYCHOTHERAPY_TIME_FIELD = "psychotherapy_time"
"""The field in that section that states the window."""

SIGNALS_AGREE_SECONDS = 120.0
"""Two proposed starts closer than this are the same start."""

StartSource = Literal["spoken_cue", "marked", "attributed"]

_TRANSCRIPT_TIME = re.compile(r"^\[?(\d+(?::\d{2}){1,2})\]?$")
_CLOCK_TIME = re.compile(r"\b\d{1,2}(?::\d{2})?\b")
_MINUTES = re.compile(r"(\d+)\s*(?:min|minute)", re.IGNORECASE)
_NOT_STATED = "not stated"
_SECONDS_PER_MINUTE = 60
_NOON = 12


@dataclass(frozen=True)
class StartCandidate:
    seconds: float
    source: StartSource


def parse_transcript_time(value: str) -> float | None:
    """Seconds for a transcript timestamp as written ("12:30", "[00:12:30]")."""
    match = _TRANSCRIPT_TIME.match(value.strip())
    if not match:
        return None
    total = 0
    for part in match.group(1).split(":"):
        total = total * _SECONDS_PER_MINUTE + int(part)
    return float(total)


def stated_clock_time(value: str) -> str | None:
    """The clinician's stated start time, kept as said, if it names a clock time."""
    text = value.strip()
    return text if _CLOCK_TIME.search(text) else None


def snap_to_turn(seconds: float, segments: Sequence[TimedSegment]) -> float | None:
    """The start of the turn nearest ``seconds``; ``None`` with no turns."""
    if not segments:
        return None
    return min((s.start for s in segments), key=lambda start: abs(start - seconds))


def resolve_start_candidates(
    *, marked: float | None, cued: bool, attributed: float | None
) -> list[StartCandidate]:
    """The starts to offer, best first (see the module docstring)."""
    if marked is not None and cued:
        return [StartCandidate(marked, "spoken_cue")]
    candidates: list[StartCandidate] = []
    if marked is not None:
        candidates.append(StartCandidate(marked, "marked"))
    if attributed is not None and (
        marked is None or abs(attributed - marked) > SIGNALS_AGREE_SECONDS
    ):
        candidates.append(StartCandidate(attributed, "attributed"))
    return candidates


def client_present_turns(
    segments: Sequence[TimedSegment], client_present_end: float | None
) -> list[TimedSegment]:
    """The turns the therapy portion can start on: before the client left."""
    if client_present_end is None:
        return list(segments)
    return [s for s in segments if s.start < client_present_end]


def window_minutes(start_seconds: float, end_seconds: float) -> int:
    """Whole minutes from start to end, rounded down."""
    return max(math.floor((end_seconds - start_seconds) / _SECONDS_PER_MINUTE), 0)


def _clock(moment: datetime, zone: tzinfo) -> str:
    local = moment.astimezone(zone)
    hour = local.hour % _NOON or _NOON
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < _NOON else 'PM'}"


def window_text(
    minutes: int,
    *,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    zone: tzinfo | None = None,
) -> str:
    """The window as a coded note states it: clock times, then minutes."""
    if start_at is not None and end_at is not None and zone is not None:
        return f"{_clock(start_at, zone)} to {_clock(end_at, zone)}, {minutes} minutes"
    return f"{minutes} minutes"


def drafted_time(content: dict[str, Any] | None) -> str | None:
    """What the note's psychotherapy time field holds, unless blank or "Not stated."."""
    section = (content or {}).get(PSYCHOTHERAPY_SECTION_KEY)
    if not isinstance(section, dict):
        return None
    value = str(section.get(PSYCHOTHERAPY_TIME_FIELD) or "").strip()
    if not value or value.rstrip(".").lower() == _NOT_STATED:
        return None
    return value


def disagrees(dictated: str | None, confirmed: dict[str, Any] | None) -> bool:
    """Whether a time already in the note disagrees with the confirmed window.

    Agreement is the same text, or the same number of minutes. Once the
    clinician chose to keep the dictated time, there is nothing to resolve.
    """
    if (
        dictated is None
        or not confirmed
        or confirmed.get("keep_dictated")
        or dictated == confirmed.get("window_text")
    ):
        return False
    stated = _MINUTES.search(dictated)
    return stated is None or int(stated.group(1)) != confirmed.get("minutes")


def clear_drafted_time(content: dict[str, Any]) -> dict[str, Any]:
    """``content`` with its psychotherapy time field emptied."""
    cleared = copy.deepcopy(content)
    cleared[PSYCHOTHERAPY_SECTION_KEY][PSYCHOTHERAPY_TIME_FIELD] = ""
    return cleared


def apply_confirmed_window(
    content: dict[str, Any] | None, window: dict[str, Any] | None
) -> dict[str, Any] | None:
    """``content`` with the confirmed window in its psychotherapy time field.

    Fills the field only where it is blank or "Not stated."; a time the
    clinician dictated stays, and :func:`disagrees` reports any conflict.
    """
    confirmed = (window or {}).get("confirmed")
    if (
        not confirmed
        or content is None
        or not isinstance(content.get(PSYCHOTHERAPY_SECTION_KEY), dict)
        or drafted_time(content) is not None
    ):
        return content
    filled = copy.deepcopy(content)
    filled[PSYCHOTHERAPY_SECTION_KEY][PSYCHOTHERAPY_TIME_FIELD] = confirmed["window_text"]
    return filled


__all__ = [
    "PSYCHOTHERAPY_SECTION_KEY",
    "PSYCHOTHERAPY_TIME_FIELD",
    "SIGNALS_AGREE_SECONDS",
    "StartCandidate",
    "apply_confirmed_window",
    "clear_drafted_time",
    "client_present_turns",
    "disagrees",
    "drafted_time",
    "parse_transcript_time",
    "resolve_start_candidates",
    "snap_to_turn",
    "stated_clock_time",
    "window_minutes",
    "window_text",
]
