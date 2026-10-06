# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where the client left the recording, and what the clinician said after.

A recording has two channels, labelled by where the audio came from:
``Therapist`` (the clinician's microphone) and ``Client`` (the call). A
clinician often keeps recording after the client has gone and dictates what
the note needs: risk, mental status, checks made, decisions and why. That
tail must reach the draft, and it must not count as face-to-face time.

The boundary is the end of the client's last substantive turn. Everything the
clinician says after it is the *clinician addendum*. A short client-channel
segment ("Okay.", a cough transcribed as a word) after a long monologue is
noise, not the client returning, so segments under
:data:`MIN_BOUNDARY_CLIENT_WORDS` words do not move the boundary.

The boundary is in seconds from the start of the recording. ``0.0`` means
the client was never present (a dictation-only recording): the whole
recording is addendum. ``None`` means it cannot be known from the channels —
an in-person recording puts both voices on the one microphone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .transcript_normalize import normalize_transcript_to_canonical_lines

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..models import Transcript

MIN_BOUNDARY_CLIENT_WORDS = 3
"""A client-channel segment shorter than this does not mark the client as present."""

SPEECH_WORDS_PER_SECOND = 2.5
"""Speaking rate used to estimate a turn's end when only its start is known."""

CLIENT_SPEAKER = "Client"

_CANONICAL_LINE = re.compile(r"^\[(\d+(?::\d{2}){1,2})\]\s*([^:]+):\s*(.*)$")
_SECONDS_PER_UNIT = 60


@dataclass(frozen=True)
class TimedSegment:
    """One turn of the recording, with its speaker and position in seconds."""

    speaker: str
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class TranscriptSplit:
    """A transcript divided at the client-present boundary."""

    session_lines: str
    addendum_lines: str


def is_call(video_platform: str | None) -> bool:
    """True when the session was a video call, so the client had a channel."""
    return video_platform not in (None, "", "none")


def is_client_speaker(speaker: str) -> bool:
    """True for the client channel, including a diarized "Client A"."""
    return speaker == CLIENT_SPEAKER or speaker.startswith(f"{CLIENT_SPEAKER} ")


def _word_count(text: str) -> int:
    return len(text.split())


def _timestamp_seconds(stamp: str) -> float:
    total = 0
    for part in stamp.split(":"):
        total = total * _SECONDS_PER_UNIT + int(part)
    return float(total)


def _format_offset(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // _SECONDS_PER_UNIT:02d}:{whole % _SECONDS_PER_UNIT:02d}"


def segments_from_transcript(transcript: Transcript) -> list[TimedSegment]:
    """Timed segments from a stored transcript, in recording order.

    Stored text carries each turn's start only. Its end is estimated from its
    length at :data:`SPEECH_WORDS_PER_SECOND`, never past the next turn's
    start. An unparseable transcript yields no segments.
    """
    try:
        canonical = normalize_transcript_to_canonical_lines(transcript.content, transcript.format)
    except ValueError:
        return []
    starts: list[tuple[str, str, float]] = []
    for line in canonical.splitlines():
        match = _CANONICAL_LINE.match(line.strip())
        if match:
            stamp, speaker, text = match.groups()
            starts.append((speaker.strip(), text.strip(), _timestamp_seconds(stamp)))
    starts.sort(key=lambda s: s[2])

    segments: list[TimedSegment] = []
    for i, (speaker, text, start) in enumerate(starts):
        end = start + _word_count(text) / SPEECH_WORDS_PER_SECOND
        later_starts = [s[2] for s in starts[i + 1 :] if s[2] > start]
        if later_starts:
            end = min(end, later_starts[0])
        segments.append(TimedSegment(speaker=speaker, text=text, start=start, end=end))
    return segments


def segments_from_utterances(utterances: Iterable[dict[str, Any]]) -> list[TimedSegment]:
    """Timed segments from a transcription provider's utterances (real ends)."""
    segments = [
        TimedSegment(
            speaker=str(u.get("speaker") or ""),
            text=str(u.get("text") or ""),
            start=float(u.get("start") or 0),
            end=float(u.get("end") or u.get("start") or 0),
        )
        for u in utterances
    ]
    return sorted(segments, key=lambda s: s.start)


def client_present_end(
    segments: Sequence[TimedSegment], *, client_channel_expected: bool
) -> float | None:
    """Seconds into the recording when the client was last present.

    ``client_channel_expected`` is true for a call, where the client's voice
    has its own channel; with no client turn at all such a recording is a
    dictation and the answer is ``0.0``. Without a client channel to expect,
    no client turn means the boundary is unknown (``None``).
    """
    substantive = [
        s
        for s in segments
        if is_client_speaker(s.speaker) and _word_count(s.text) >= MIN_BOUNDARY_CLIENT_WORDS
    ]
    if substantive:
        return max(s.end for s in substantive)
    if client_channel_expected or any(is_client_speaker(s.speaker) for s in segments):
        return 0.0
    return None


def recording_end(segments: Sequence[TimedSegment]) -> float:
    """End of the last turn, in seconds."""
    return max((s.end for s in segments), default=0.0)


DICTATED_HEADING = "Dictated by the clinician after the session (the client was not present):"
"""Heads what the clinician dictated for the note after the recording stopped.

A redraft appends those dictations to the session's transcript under this
heading; they are not part of the recording and carry no recording times.
"""


def split_dictated(content: str) -> tuple[str, str]:
    """The recording's transcript, and what was dictated for the note after it."""
    recording, _, dictated = content.partition(DICTATED_HEADING)
    return recording.rstrip(), dictated.strip()


def split_at_boundary(segments: Sequence[TimedSegment], boundary: float) -> TranscriptSplit:
    """The turns before the boundary, and the clinician's turns after it.

    Client-channel turns after the boundary are the sub-threshold noise the
    boundary ignored; they belong to neither part.
    """
    session: list[str] = []
    addendum: list[str] = []
    for s in segments:
        line = f"[{_format_offset(s.start)}] {s.speaker}: {s.text}"
        if s.start < boundary:
            session.append(line)
        elif not is_client_speaker(s.speaker):
            addendum.append(line)
    return TranscriptSplit(session_lines="\n".join(session), addendum_lines="\n".join(addendum))


__all__ = [
    "CLIENT_SPEAKER",
    "DICTATED_HEADING",
    "MIN_BOUNDARY_CLIENT_WORDS",
    "SPEECH_WORDS_PER_SECOND",
    "TimedSegment",
    "TranscriptSplit",
    "client_present_end",
    "is_call",
    "is_client_speaker",
    "recording_end",
    "segments_from_transcript",
    "segments_from_utterances",
    "split_at_boundary",
    "split_dictated",
]
