# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Benchmark faster-whisper on CPU int8 against the default provider.

Reads sessions written by ``transcription_benchmark_fixtures.py`` (two mono
tracks per session plus a reference transcript) and, for each Whisper model
size, transcribes both tracks through ``TranscriptionWorker.transcribe_channel``,
the call every transcription job makes. Per model it reports:

- **WER**: word error rate per channel, pooled over every session. A word
  heard on the wrong channel counts against both channels, so WER already
  pays for misattribution.
- **Attribution**: of the transcribed words that match a word of the
  reference conversation, the share that came out of the channel of the
  speaker who said it. Words matching nothing (misrecognitions,
  hallucinations in the silence) are counted on their own as unmatched.
- **s / audio min**: wall-clock seconds spent transcribing both tracks, per
  minute of session. Model load is reported separately.
- **Peak RSS**: of a fresh process that loaded only that model.

CPU and int8 only. Each model runs in its own child process, so one model's
memory never inflates another's peak.

When ``ASSEMBLYAI_API_KEY`` is set, the default provider transcribes the same
tracks, one job per channel as the backend submits them, and its WER and
attribution join the table. Without the key that row is skipped. The provider
transcripts are deleted once scored.

At the fixtures' default bleed the larger models transcribe the other speaker
too, so run a clean set
(``--no-bleed``) beside it to separate recognition from attribution.

Run it from the worker's environment (see ``transcription_decode_probe.py``):

    python scripts/transcription_benchmark.py /tmp/tx-fixtures --models tiny base
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

WORKER_DIR = Path(__file__).resolve().parent.parent / "services" / "transcription"
MODELS = ("tiny", "base", "small", "medium", "large-v3-turbo")
SPEAKERS = ("Therapist", "Client")
TRACKS = {"Therapist": "therapist.wav", "Client": "client.wav"}
PROVIDER_ROW = "default provider"
ASSEMBLYAI_API_BASE = "https://api.assemblyai.com/v2"


class Segment(Protocol):
    """What the scoring needs from a transcript segment (``LabeledSegment`` fits)."""

    start: float
    end: float
    speaker: str
    text: str


@dataclass
class Word:
    start: float
    end: float
    speaker: str
    text: str


# --- Scoring -----------------------------------------------------------------

_KEEP = re.compile(r"[^a-z0-9' ]+")


def normalize_words(text: str) -> list[str]:
    """Lower-case, drop punctuation (apostrophes stay inside words), split."""
    cleaned = _KEEP.sub(" ", text.lower().replace("-", " "))
    return [w.strip("'") for w in cleaned.split() if w.strip("'")]


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    """Word-level Levenshtein distance: substitutions + deletions + insertions."""
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            current.append(
                min(
                    previous[j] + 1,  # deletion
                    current[j - 1] + 1,  # insertion
                    previous[j - 1] + (ref_word != hyp_word),  # substitution / match
                )
            )
        previous = current
    return previous[-1]


@dataclass
class Score:
    """Sums that pool across sessions; the rates are derived."""

    ref_words: int = 0
    edits: int = 0
    attributed_right: int = 0
    attributed_wrong: int = 0
    unmatched: int = 0

    def add(self, other: Score) -> None:
        for f in dataclasses.fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    @property
    def wer(self) -> float:
        return self.edits / self.ref_words if self.ref_words else 0.0

    @property
    def attribution(self) -> float:
        matched = self.attributed_right + self.attributed_wrong
        return self.attributed_right / matched if matched else 0.0


def matched_pairs(
    ref: list[str], hyp: list[str], preferred: list[bool] | None = None
) -> list[tuple[int, int]]:
    """Index pairs ``(i, j)`` with ``ref[i] == hyp[j]`` in a longest common subsequence.

    Among alignments with the most matches, one matching the most
    ``preferred`` reference words wins: each match is worth more than every
    preference bonus together, so a preference never costs a match.
    """
    rows, cols = len(ref), len(hyp)
    weight = cols + 1

    def value(i: int) -> int:
        return weight + (1 if preferred and preferred[i] else 0)

    best = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        for j in range(cols - 1, -1, -1):
            skip = max(best[i + 1][j], best[i][j + 1])
            take = best[i + 1][j + 1] + value(i) if ref[i] == hyp[j] else 0
            best[i][j] = max(skip, take)
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < rows and j < cols:
        if ref[i] == hyp[j] and best[i][j] == best[i + 1][j + 1] + value(i):
            pairs.append((i, j))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs


def _channel_words(segments: list[Segment], speaker: str) -> list[str]:
    ordered = sorted(segments, key=lambda s: s.start)
    return [w for s in ordered if s.speaker == speaker for w in normalize_words(s.text)]


def score_session(turns: list[dict[str, Any]], segments: list[Segment]) -> Score:
    """Score one session's channel-labelled segments against its reference turns.

    WER is computed per channel: the reference words of that speaker's turns
    against the words transcribed on that speaker's channel, both in time
    order.

    Attribution aligns each channel's words against the whole conversation
    (both speakers, in turn order). A word that matches a reference word is
    right when that word was said by the channel's speaker and wrong when it
    is the other speaker's, heard through bleed. A word that matches nothing
    is unmatched. A word both speakers said, with nothing around it to place
    it, goes to the channel's own speaker. Text alignment, not timestamps: a segment's timing spans
    the silences on its own track, so it cannot place a word in a turn.
    """
    score = Score()
    conversation = [
        (t["speaker"], w)
        for t in sorted(turns, key=lambda t: t["start"])
        for w in normalize_words(t["text"])
    ]
    for speaker in SPEAKERS:
        ref = [w for who, w in conversation if who == speaker]
        hyp = _channel_words(segments, speaker)
        score.ref_words += len(ref)
        score.edits += edit_distance(ref, hyp)

        pairs = matched_pairs(
            [w for _, w in conversation], hyp, [who == speaker for who, _ in conversation]
        )
        right = sum(1 for i, _ in pairs if conversation[i][0] == speaker)
        score.attributed_right += right
        score.attributed_wrong += len(pairs) - right
        score.unmatched += len(hyp) - len(pairs)
    return score


@dataclass
class Result:
    model: str
    score: Score = field(default_factory=Score)
    audio_seconds: float = 0.0
    transcribe_seconds: float | None = None
    load_seconds: float | None = None
    peak_rss_mb: float | None = None
    error: str | None = None

    @property
    def seconds_per_audio_minute(self) -> float | None:
        if self.transcribe_seconds is None or not self.audio_seconds:
            return None
        return self.transcribe_seconds / (self.audio_seconds / 60)


def _cell(value: float | None, fmt: str) -> str:
    return "n/a" if value is None else format(value, fmt)


def render_table(results: list[Result]) -> str:
    lines = [
        "| Model | WER | Attribution | Unmatched words | s / audio min | Load s | Peak RSS MB |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.error:
            lines.append(f"| {r.model} | failed: {r.error} | | | | | |")
            continue
        lines.append(
            f"| {r.model} | {r.score.wer:.1%} | {r.score.attribution:.1%} | {r.score.unmatched} "
            f"| {_cell(r.seconds_per_audio_minute, '.1f')} | {_cell(r.load_seconds, '.1f')} "
            f"| {_cell(r.peak_rss_mb, '.0f')} |"
        )
    return "\n".join(lines)


# --- Sessions ----------------------------------------------------------------


def load_sessions(fixtures: Path) -> list[tuple[Path, dict[str, Any]]]:
    sessions = [
        (ref.parent, json.loads(ref.read_text()))
        for ref in sorted(fixtures.glob("*/reference.json"))
    ]
    if not sessions:
        raise SystemExit(f"No sessions under {fixtures}: run transcription_benchmark_fixtures.py")
    return sessions


def _peak_rss_mb() -> float:
    import resource  # noqa: PLC0415 — POSIX only; the child is the only caller

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def run_whisper(model: str, fixtures: Path) -> Result:
    """Transcribe every session with one model, in this process."""
    sys.path.insert(0, str(WORKER_DIR))
    from config import TranscriptionSettings  # noqa: PLC0415 — worker env only
    from worker import TranscriptionWorker  # noqa: PLC0415 — worker env only

    worker = TranscriptionWorker(
        TranscriptionSettings(
            whisper_model_size=model, whisper_device="cpu", whisper_compute_type="int8"
        )
    )
    result = Result(model=model)
    transcribing = 0.0
    started = time.perf_counter()
    _ = worker.model
    result.load_seconds = time.perf_counter() - started

    for directory, reference in load_sessions(fixtures):
        started = time.perf_counter()
        segments = [
            seg
            for speaker in SPEAKERS
            for seg in worker.transcribe_channel(directory / TRACKS[speaker], speaker)
        ]
        transcribing += time.perf_counter() - started
        result.audio_seconds += reference["duration"]
        result.score.add(score_session(reference["turns"], segments))
    result.transcribe_seconds = transcribing
    result.peak_rss_mb = _peak_rss_mb()
    return result


def run_whisper_in_child(model: str, fixtures: Path) -> Result:
    proc = subprocess.run(  # noqa: S603 — this script, re-run with a model from MODELS
        [sys.executable, __file__, str(fixtures), "--child", model],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or [f"exit {proc.returncode}"])[-1]
        if proc.returncode < 0:
            tail = f"killed by signal {-proc.returncode} (out of memory?)"
        return Result(model=model, error=tail[:120])
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    data["score"] = Score(**data["score"])
    return Result(**data)


def _provider_words(api_key: str, path: Path, speaker: str) -> list[Word]:
    """One job per channel, as the backend submits them; the transcript is deleted after."""
    import httpx  # noqa: PLC0415 — only needed with a key

    headers = {"Authorization": api_key}
    uploaded = httpx.post(
        f"{ASSEMBLYAI_API_BASE}/upload",
        headers={**headers, "Content-Type": "application/octet-stream"},
        content=path.read_bytes(),
        timeout=120,
    )
    uploaded.raise_for_status()
    submitted = httpx.post(
        f"{ASSEMBLYAI_API_BASE}/transcript",
        headers=headers,
        json={
            "audio_url": uploaded.json()["upload_url"],
            "language_code": "en",
            "speech_model": os.environ.get("ASSEMBLYAI_SPEECH_MODEL", "best"),
        },
        timeout=30,
    )
    submitted.raise_for_status()
    transcript_id = submitted.json()["id"]
    url = f"{ASSEMBLYAI_API_BASE}/transcript/{transcript_id}"
    try:
        deadline = time.monotonic() + 600
        while True:
            data = httpx.get(url, headers=headers, timeout=30).json()
            if data["status"] == "completed":
                break
            if data["status"] == "error" or time.monotonic() > deadline:
                raise RuntimeError(f"transcript {data['status']}")
            time.sleep(3)
    finally:
        httpx.delete(url, headers=headers, timeout=30)
    return [
        Word(start=w["start"] / 1000, end=w["end"] / 1000, speaker=speaker, text=w["text"])
        for w in data.get("words") or []
    ]


def run_provider(api_key: str, fixtures: Path) -> Result:
    result = Result(model=PROVIDER_ROW)
    try:
        for directory, reference in load_sessions(fixtures):
            words: list[Segment] = []
            for speaker in SPEAKERS:
                words.extend(_provider_words(api_key, directory / TRACKS[speaker], speaker))
            result.audio_seconds += reference["duration"]
            result.score.add(score_session(reference["turns"], words))
    except Exception as exc:  # one failed row must not lose the table
        result.error = type(exc).__name__
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("fixtures", type=Path, help="directory of synthesized sessions")
    parser.add_argument("--models", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--out", type=Path, help="also write the markdown table here")
    parser.add_argument("--child", choices=MODELS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child:
        result = run_whisper(args.child, args.fixtures)
        print(json.dumps(dataclasses.asdict(result)))
        return 0

    sessions = load_sessions(args.fixtures)
    minutes = sum(r["duration"] for _, r in sessions) / 60
    print(f"{len(sessions)} sessions, {minutes:.1f} audio minutes, CPU int8", file=sys.stderr)
    results: list[Result] = []
    for model in args.models:
        print(f"... {model}", file=sys.stderr)
        results.append(run_whisper_in_child(model, args.fixtures))

    api_key = os.environ.get("ASSEMBLYAI_API_KEY", "")
    if api_key:
        print(f"... {PROVIDER_ROW}", file=sys.stderr)
        results.append(run_provider(api_key, args.fixtures))
    else:
        print(f"ASSEMBLYAI_API_KEY not set: skipping the {PROVIDER_ROW} row", file=sys.stderr)

    table = render_table(results)
    print(table)
    if args.out:
        args.out.write_text(table + "\n")
    return 1 if any(r.error for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
