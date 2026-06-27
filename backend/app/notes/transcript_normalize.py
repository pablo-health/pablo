# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Normalize an uploaded transcript into ``[MM:SS] Speaker: text`` lines.

A single transcript can arrive in one of four shapes (see
:class:`app.models.enums.TranscriptFormat`):

- ``txt`` — already in ``[MM:SS] Speaker: text`` form. Pass through.
- ``vtt`` — Zoom-style WebVTT with explicit start/end timestamps.
- ``google_meet`` — bracketed-timestamp text
  (``[00:00:08]\\nName: text``). Also catches generic Zoom text dumps
  that follow the same shape.
- ``json`` — list of segment dicts (``{participant, text,
  start_timestamp, end_timestamp}``) matching the
  ``meeting_transcription`` combined format.

All four shapes are normalized to the same canonical line format so the
SOAP prompt builder and the source-attribution Call-2 see identical
input regardless of upload source.

Lifted-down from the historical ``meeting_transcription.pipeline``
helpers (THERAPY-71d5 / 9ijg): we keep the parsing intent and discard
the intermediate JSON-dict round-trip that existed only for the
plugin's file-I/O protocol.
"""

from __future__ import annotations

import json
import re
from typing import Any

_VTT_TIMESTAMP = re.compile(r"^(\d{2}:\d{2}:\d{2})\.\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}\.\d{3}$")
_BRACKETED_TIMESTAMP = re.compile(r"^\[(\d{2}:\d{2}:\d{2}|\d{2}:\d{2})\]$")
_SPEAKER_LINE = re.compile(r"^([^:\n]+):\s+(.+)$")


def normalize_transcript_to_canonical_lines(
    content: str,
    transcript_format: str,
) -> str:
    """Return the transcript as newline-joined ``[MM:SS] Speaker: text``.

    Empty lines are dropped. Unknown formats raise :class:`ValueError`.
    """
    fmt = transcript_format.lower()
    if fmt == "txt":
        return _normalize_txt(content)
    if fmt == "vtt":
        return _normalize_vtt(content)
    if fmt == "google_meet":
        return _normalize_bracketed(content)
    if fmt == "json":
        return _normalize_json(content)
    raise ValueError(f"Unsupported transcript format: {transcript_format!r}")


# ---------------------------------------------------------------------------
# Per-format helpers
# ---------------------------------------------------------------------------


def _normalize_txt(content: str) -> str:
    """``txt`` is already canonical — strip blanks and return."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return "\n".join(lines)


def _normalize_vtt(content: str) -> str:
    """Parse Zoom-style WebVTT and emit canonical lines.

    Each cue is a ``HH:MM:SS.mmm --> HH:MM:SS.mmm`` line followed by one
    or more ``Speaker: text`` lines. The cue's start timestamp becomes
    the ``[MM:SS]`` prefix; if the cue lacks a speaker prefix, the prior
    speaker is reused (and if there is none, ``"Unknown Speaker"``).
    """
    out: list[str] = []
    current_speaker: str | None = None
    cue_start: str | None = None
    cue_lines: list[str] = []

    def _flush() -> None:
        nonlocal cue_lines
        if cue_start is None or not cue_lines:
            cue_lines = []
            return
        speaker = current_speaker or "Unknown Speaker"
        text = " ".join(cue_lines).strip()
        if text:
            out.append(f"[{_hhmmss_to_mmss(cue_start)}] {speaker}: {text}")
        cue_lines = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line == "WEBVTT":
            continue
        ts_match = _VTT_TIMESTAMP.match(line)
        if ts_match:
            _flush()
            cue_start = ts_match.group(1)
            continue
        speaker_match = _SPEAKER_LINE.match(line)
        if speaker_match:
            _flush()
            current_speaker = speaker_match.group(1).strip()
            cue_lines = [speaker_match.group(2).strip()]
            continue
        # Continuation line — append to whatever speaker is current.
        cue_lines.append(line)
    _flush()
    return "\n".join(out)


def _normalize_bracketed(content: str) -> str:
    """Parse Google Meet / generic-bracketed transcripts.

    Format:

        [00:00:08]
        Sarah Chen: Hello everyone

    A bare ``[HH:MM:SS]`` (or ``[MM:SS]``) line sets the timestamp for
    the *next* ``Speaker: text`` line.
    """
    out: list[str] = []
    current_ts: str | None = None
    current_speaker: str | None = None
    current_text: list[str] = []

    def _flush() -> None:
        nonlocal current_text
        if current_speaker is None or not current_text:
            current_text = []
            return
        text = " ".join(current_text).strip()
        if text:
            out.append(
                f"[{_normalize_bracket_ts(current_ts) or '00:00'}] {current_speaker}: {text}"
            )
        current_text = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        ts_match = _BRACKETED_TIMESTAMP.match(line)
        if ts_match:
            _flush()
            current_ts = ts_match.group(1)
            continue
        speaker_match = _SPEAKER_LINE.match(line)
        if speaker_match:
            _flush()
            current_speaker = speaker_match.group(1).strip()
            current_text = [speaker_match.group(2).strip()]
            continue
        current_text.append(line)
    _flush()
    return "\n".join(out)


def _normalize_json(content: str) -> str:
    """Parse a JSON list of segment dicts (combined-transcript shape).

    Tolerant to two variants:

    - ``{"participant": {"name": ...}, "text": ..., "start_timestamp": {"relative": secs}}``
    - ``{"speaker": ..., "text": ..., "start": secs}`` (plain shape)
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON transcript is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("JSON transcript must be a list of segments")

    out: list[str] = []
    for segment in data:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        speaker = _extract_speaker(segment)
        start_seconds = _extract_start_seconds(segment)
        out.append(f"[{_seconds_to_mmss(start_seconds)}] {speaker}: {text}")
    return "\n".join(out)


def _extract_speaker(segment: dict[str, Any]) -> str:
    participant = segment.get("participant")
    if isinstance(participant, dict):
        name = participant.get("name")
        if name:
            return str(name)
    raw = segment.get("speaker")
    if raw:
        return str(raw)
    return "Unknown Speaker"


def _extract_start_seconds(segment: dict[str, Any]) -> float:
    start = segment.get("start_timestamp")
    if isinstance(start, dict):
        relative = start.get("relative")
        if isinstance(relative, (int, float)):
            return float(relative)
    raw = segment.get("start")
    if isinstance(raw, (int, float)):
        return float(raw)
    return 0.0


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def _hhmmss_to_mmss(ts: str) -> str:
    """``"00:01:23"`` → ``"01:23"`` (or ``"83:45"`` for >1h)."""
    hours, minutes, seconds = (int(p) for p in ts.split(":"))
    total_minutes = hours * 60 + minutes
    return f"{total_minutes:02d}:{seconds:02d}"


_TS_PARTS_HHMMSS = 3
_TS_PARTS_MMSS = 2


def _normalize_bracket_ts(ts: str | None) -> str | None:
    """``"00:01:23"`` → ``"01:23"``; ``"01:23"`` left alone."""
    if ts is None:
        return None
    parts = ts.split(":")
    if len(parts) == _TS_PARTS_HHMMSS:
        return _hhmmss_to_mmss(ts)
    if len(parts) == _TS_PARTS_MMSS:
        return ts
    return None


def _seconds_to_mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


__all__ = ["normalize_transcript_to_canonical_lines"]
