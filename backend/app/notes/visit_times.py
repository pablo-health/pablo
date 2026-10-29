# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The psychotherapy time of a visit: which turns were therapy, and how long.

A visit that pairs a medication check with psychotherapy bills the therapy
by its face-to-face minutes, so those minutes count the therapy only, with
the client present (:mod:`app.notes.client_present`). The therapy and the
medication or screening questions often interleave: a risk question asked
late in the visit because it would have landed wrong at the start. So the
time is not one window from where the therapy began to where the client
left. Every client-present turn carries a label, and the therapy minutes
are the sum of the turns labeled therapy.

The labels are proposed from the transcript (:mod:`app.services.therapy_labels`)
and confirmed by the clinician, who can relabel any run of turns. A turn
spans from its start to the next turn's start, and the last client-present
turn ends where the client left: the sum therefore lines up with turns,
never counts the clinician's dictated tail, and never exceeds the
client-present span. A turn with no label is unattributed and not counted.

Once confirmed, the time is written into the note's psychotherapy time
field. When the therapy was one contiguous run, the field states its clock
window and minutes; otherwise the minutes, and that the therapy was
interleaved with the medication management.

A time the clinician dictated wins. The draft returns it as a
:class:`DictatedTime` (start, end and minutes as the clinician stated them),
which is rendered into the field; whether it agrees with the confirmed time
is a comparison of its minutes, never a reading of the field's text. Where
the dictated time names no clock time and its minutes match the confirmed
time, the confirmed time completes it.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, get_args

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime, tzinfo

    from .client_present import TimedSegment

PSYCHOTHERAPY_SECTION_KEY = "psychotherapy"
"""The section a type carries for a visit's psychotherapy portion."""

PSYCHOTHERAPY_TIME_FIELD = "psychotherapy_time"
"""The field in that section that states the time."""

TurnLabel = Literal["therapy", "medication_management", "screening_risk", "admin"]
TURN_LABELS: tuple[TurnLabel, ...] = get_args(TurnLabel)
THERAPY: TurnLabel = "therapy"

RunLabel = Literal["therapy", "medication_management", "screening_risk", "admin", "unattributed"]
UNATTRIBUTED: RunLabel = "unattributed"

INTERLEAVED_NOTE = "interleaved with medication management; time accounted separately"
"""What the note says beside therapy minutes that were not one contiguous run."""

_SECONDS_PER_MINUTE = 60
_NOON = 12


@dataclass(frozen=True)
class DictatedTime:
    """A psychotherapy time the clinician stated, part by part, as said.

    ``start`` and ``end`` are clock times kept exactly as said ("around
    10:15"); ``minutes`` is a count the clinician said; ``as_dictated`` is
    the clinician's words. A part the clinician did not state is ``None``.
    """

    start: str | None = None
    end: str | None = None
    minutes: int | None = None
    as_dictated: str = ""

    @property
    def names_a_clock_time(self) -> bool:
        return self.start is not None or self.end is not None

    @classmethod
    def from_reply(cls, value: Any) -> DictatedTime | None:
        """The stated time in a model reply or a stored proposal; ``None`` if none was stated."""
        if not isinstance(value, dict):
            return None
        minutes = value.get("minutes")
        stated = cls(
            start=_part(value.get("start")),
            end=_part(value.get("end")),
            # A count of zero or less is no count the clinician would state.
            minutes=minutes if isinstance(minutes, int) and minutes > 0 else None,
            as_dictated=_part(value.get("as_dictated")) or "",
        )
        if stated.minutes is None and not stated.names_a_clock_time:
            return None
        return stated

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "minutes": self.minutes,
            "as_dictated": self.as_dictated,
        }


def _part(value: Any) -> str | None:
    text = str(value).strip() if isinstance(value, str | int | float) else ""
    return text or None


def dictated_time_text(dictated: DictatedTime | None) -> str:
    """The note's line for a dictated time: its clock times, then its minutes."""
    if dictated is None:
        return ""
    parts: list[str] = []
    if dictated.start and dictated.end:
        parts.append(f"{dictated.start} to {dictated.end}")
    elif dictated.start:
        parts.append(f"Started {dictated.start}")
    elif dictated.end:
        parts.append(f"Ended {dictated.end}")
    if dictated.minutes is not None:
        parts.append(f"{dictated.minutes} minutes")
    return ", ".join(parts)


@dataclass(frozen=True)
class Run:
    """Consecutive client-present turns with the same label, in seconds into the recording."""

    label: RunLabel
    start: float
    end: float


def client_present_turns(
    segments: Sequence[TimedSegment], client_present_end: float | None
) -> list[TimedSegment]:
    """The turns before the client left: the only ones a label can count."""
    if client_present_end is None:
        return list(segments)
    return [s for s in segments if s.start < client_present_end]


def turn_spans(segments: Sequence[TimedSegment], end: float) -> list[tuple[float, float]]:
    """Each client-present turn's span: from its start to the next turn's, the last to ``end``."""
    starts = sorted({s.start for s in segments if 0 <= s.start < end})
    return [
        (start, starts[i + 1] if i + 1 < len(starts) else end) for i, start in enumerate(starts)
    ]


def layout(
    labels: Mapping[float, TurnLabel], segments: Sequence[TimedSegment], end: float
) -> list[Run]:
    """The client-present span as runs of one label each, in order.

    ``labels`` maps a turn's start, in seconds, to its label; a turn missing
    from it is unattributed.
    """
    runs: list[Run] = []
    for start, stop in turn_spans(segments, end):
        label: RunLabel = labels.get(start, UNATTRIBUTED)
        if runs and runs[-1].label == label:
            runs[-1] = Run(label, runs[-1].start, stop)
        else:
            runs.append(Run(label, start, stop))
    return runs


def therapy_seconds(
    labels: Mapping[float, TurnLabel], segments: Sequence[TimedSegment], end: float
) -> float:
    """Seconds of the client-present turns labeled therapy."""
    return sum(r.end - r.start for r in layout(labels, segments, end) if r.label == THERAPY)


def therapy_minutes(
    labels: Mapping[float, TurnLabel], segments: Sequence[TimedSegment], end: float
) -> int:
    """Whole minutes of therapy, rounded down: never more than the client was present."""
    return math.floor(therapy_seconds(labels, segments, end) / _SECONDS_PER_MINUTE)


def therapy_runs(runs: Sequence[Run]) -> list[Run]:
    return [r for r in runs if r.label == THERAPY]


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
    """One contiguous run as a coded note states it: clock times, then minutes."""
    if start_at is not None and end_at is not None and zone is not None:
        return f"{_clock(start_at, zone)} to {_clock(end_at, zone)}, {minutes} minutes"
    return f"{minutes} minutes"


def interleaved_text(minutes: int) -> str:
    """Therapy minutes that were not one run: the minutes, and that they interleaved."""
    return f"{minutes} minutes ({INTERLEAVED_NOTE})"


def time_field(content: dict[str, Any] | None) -> str:
    section = (content or {}).get(PSYCHOTHERAPY_SECTION_KEY)
    if not isinstance(section, dict):
        return ""
    return str(section.get(PSYCHOTHERAPY_TIME_FIELD) or "").strip()


def proposed_dictation(window: Mapping[str, Any] | None) -> DictatedTime | None:
    """The time the clinician dictated, as the draft returned it."""
    return DictatedTime.from_reply(((window or {}).get("proposal") or {}).get("dictated"))


def drafted_time(
    content: dict[str, Any] | None, window: Mapping[str, Any] | None = None
) -> str | None:
    """What the note's psychotherapy time field holds, unless it is empty or the confirmed time.

    Whatever else is there was said or typed by the clinician: the rendered
    dictated time, or their own edit.
    """
    value = time_field(content)
    confirmed = (window or {}).get("confirmed") or {}
    if not value or value == confirmed.get("window_text"):
        return None
    return value


def _completes(value: str, dictated: DictatedTime | None, confirmed: Mapping[str, Any]) -> bool:
    """Whether the confirmed time only adds to a dictated one: no clock time, same minutes."""
    return (
        dictated is not None
        and value == dictated_time_text(dictated)
        and not dictated.names_a_clock_time
        and dictated.minutes == confirmed.get("minutes")
    )


def disagrees(content: dict[str, Any] | None, window: Mapping[str, Any] | None) -> bool:
    """Whether a time the clinician dictated disagrees with the confirmed one.

    The dictated time agrees when its minutes match; text the clinician typed
    into the field has no minutes to compare, so it disagrees until the
    clinician picks. Once the clinician chose to keep their own time, there
    is nothing to resolve.
    """
    confirmed = (window or {}).get("confirmed")
    value = drafted_time(content, window)
    if value is None or not confirmed or confirmed.get("keep_dictated"):
        return False
    dictated = proposed_dictation(window)
    if dictated is None or value != dictated_time_text(dictated):
        return True
    return bool(dictated.minutes != confirmed.get("minutes"))


def clear_drafted_time(content: dict[str, Any]) -> dict[str, Any]:
    """``content`` with its psychotherapy time field emptied."""
    cleared = copy.deepcopy(content)
    cleared[PSYCHOTHERAPY_SECTION_KEY][PSYCHOTHERAPY_TIME_FIELD] = ""
    return cleared


def apply_confirmed_window(
    content: dict[str, Any] | None, window: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    """``content`` with the confirmed time in its psychotherapy time field.

    The confirmed time is either shape: a contiguous window or interleaved
    minutes, already rendered as ``window_text``. It fills the field where
    the field is empty, completes a dictated time whose minutes it matches,
    or replaces a dictated time the clinician chose it over; any other time
    the clinician dictated or typed stays, and :func:`disagrees` reports it.
    """
    confirmed = (window or {}).get("confirmed")
    if (
        not confirmed
        or content is None
        or not isinstance(content.get(PSYCHOTHERAPY_SECTION_KEY), dict)
    ):
        return content
    value = drafted_time(content, window)
    if (
        value is not None
        and not confirmed.get("use_confirmed")
        and (
            confirmed.get("keep_dictated")
            or not _completes(value, proposed_dictation(window), confirmed)
        )
    ):
        return content
    filled = copy.deepcopy(content)
    filled[PSYCHOTHERAPY_SECTION_KEY][PSYCHOTHERAPY_TIME_FIELD] = confirmed["window_text"]
    return filled


def labels_from_stored(stored: Any) -> dict[float, TurnLabel]:
    """``[{"seconds": ..., "label": ...}]`` as stored, keyed by the turn's start."""
    labels: dict[float, TurnLabel] = {}
    for item in stored if isinstance(stored, list) else []:
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        seconds = item.get("seconds")
        if label in TURN_LABELS and isinstance(seconds, int | float):
            labels[float(seconds)] = label
    return labels


def labels_to_stored(labels: Mapping[float, TurnLabel]) -> list[dict[str, Any]]:
    return [{"seconds": s, "label": labels[s]} for s in sorted(labels)]


__all__ = [
    "INTERLEAVED_NOTE",
    "PSYCHOTHERAPY_SECTION_KEY",
    "PSYCHOTHERAPY_TIME_FIELD",
    "THERAPY",
    "TURN_LABELS",
    "UNATTRIBUTED",
    "DictatedTime",
    "Run",
    "RunLabel",
    "TurnLabel",
    "apply_confirmed_window",
    "clear_drafted_time",
    "client_present_turns",
    "dictated_time_text",
    "disagrees",
    "drafted_time",
    "interleaved_text",
    "labels_from_stored",
    "labels_to_stored",
    "layout",
    "proposed_dictation",
    "therapy_minutes",
    "therapy_runs",
    "therapy_seconds",
    "time_field",
    "turn_spans",
    "window_minutes",
    "window_text",
]
