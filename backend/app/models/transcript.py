# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Transcript domain models and parsing helpers."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from pydantic import BaseModel

from .enums import TranscriptFormat  # noqa: TC001 — Pydantic needs this at runtime


class TranscriptModel(BaseModel):
    """Transcript data model."""

    format: TranscriptFormat
    content: str


class TranscriptSegmentModel(BaseModel):
    """A single parsed transcript segment for source linking."""

    index: int
    speaker: str
    text: str
    start_time: float
    end_time: float


@dataclass
class Transcript:
    """Transcript data."""

    format: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


# --- Transcript parsing helpers ---

_MM_SS_PARTS = 2
_HH_MM_SS_PARTS = 3
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600


def _timestamp_to_seconds(ts: str) -> float:
    """Convert "MM:SS" or "HH:MM:SS" to seconds."""
    parts = ts.split(":")
    if len(parts) == _MM_SS_PARTS:
        return int(parts[0]) * _SECONDS_PER_MINUTE + int(parts[1])
    if len(parts) == _HH_MM_SS_PARTS:
        return (
            int(parts[0]) * _SECONDS_PER_HOUR + int(parts[1]) * _SECONDS_PER_MINUTE + int(parts[2])
        )
    return 0.0


def parse_transcript_segments(content: str) -> list[TranscriptSegmentModel]:
    """Parse transcript text content into structured segments.

    Handles the common format: "[HH:MM:SS] Speaker: text"
    Returns empty list if parsing fails or content is not parseable.
    """
    segments: list[TranscriptSegmentModel] = []
    # Match lines like "[00:01:05] Therapist: Hello" or "[00:05] Client: Hi"
    pattern = re.compile(r"\[(\d{1,2}:?\d{2}(?::\d{2})?)\]\s*([^:]+):\s*(.*)")

    for _idx, raw_line in enumerate(content.strip().splitlines()):
        stripped = raw_line.strip()
        if not stripped:
            continue
        match = pattern.match(stripped)
        if not match:
            continue
        timestamp_str, speaker, seg_text = match.group(1), match.group(2).strip(), match.group(3)
        seconds = _timestamp_to_seconds(timestamp_str)
        segments.append(
            TranscriptSegmentModel(
                index=0,
                speaker=speaker,
                text=seg_text.strip(),
                start_time=seconds,
                end_time=seconds,  # Same as start for line-level segments
            )
        )

    # Reindex to be contiguous 0..N
    for i, seg in enumerate(segments):
        seg.index = i

    return segments
