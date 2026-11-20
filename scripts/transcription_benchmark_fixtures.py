# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Synthesize two-speaker sessions for ``transcription_benchmark.py``.

Never real session audio. Each session is an invented dialogue spoken by a
text-to-speech engine, laid out the way the recording client delivers a
session: two mono tracks of the same length, the therapist's microphone and
the client's audio, each silent while the other speaker talks. A small,
configurable amount of each voice bleeds into the other track, as a real room
does, so speaker attribution by channel has something to measure.

Output, one directory per session::

    <out>/<session>/therapist.wav   16 kHz mono PCM
    <out>/<session>/client.wav      16 kHz mono PCM, same length
    <out>/<session>/reference.json  {"duration": s, "turns": [{speaker, start, end, text}]}

Text to speech comes from macOS ``say`` or ``espeak-ng``, whichever is on the
PATH (``--tts`` picks one). Run it from the worker's environment, which holds
the decoder used to read the engine's output:

    python scripts/transcription_benchmark_fixtures.py /tmp/tx-fixtures
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from faster_whisper import decode_audio

SAMPLE_RATE = 16000
LEAD_IN_SECONDS = 1.0
GAP_SECONDS = 0.7
DEFAULT_BLEED_DB = -30.0

# Invented dialogues. Words only, no numerals, so the reference has one
# spelling and the score measures recognition rather than formatting.
SESSIONS: dict[str, list[tuple[str, str]]] = {
    "check-in": [
        ("Therapist", "Good afternoon. How has the week been since we last talked?"),
        ("Client", "Honestly it was a mixed week. Work was busy and I slept badly."),
        ("Therapist", "What made the sleep hard, do you think?"),
        (
            "Client",
            "I kept going over a conversation with my manager. I could not switch my mind off.",
        ),
        ("Therapist", "That sounds tiring. Did you try the breathing exercise we practised?"),
        ("Client", "I tried it twice. The second time it helped a little."),
        ("Therapist", "That is worth noticing. What was different the second time?"),
        ("Client", "I did it earlier in the evening, before I was already wound up."),
        (
            "Therapist",
            "So timing matters. Shall we plan to try it at the same hour each night this week?",
        ),
        ("Client", "Yes, I can do that. I will set a reminder on my phone."),
    ],
    "planning": [
        ("Therapist", "Last time you mentioned wanting to walk more. How did that go?"),
        ("Client", "I walked on Monday and Thursday, but the weekend got away from me."),
        ("Therapist", "Two walks is a real start. What got in the way at the weekend?"),
        ("Client", "Family visited, and I felt I should stay home with everyone."),
        ("Therapist", "Could a walk include them, or would you rather it stayed your own time?"),
        ("Client", "I think I would like it to stay mine. It is the quiet part of my day."),
        ("Therapist", "Then let us protect it. Which mornings feel most realistic?"),
        ("Client", "Tuesday and Saturday mornings, before anyone else is awake."),
        ("Therapist", "Good. We can check in on how those mornings went next time."),
        ("Client", "Thank you. That feels manageable."),
    ],
}

# (therapist voice, client voice) per engine.
DEFAULT_VOICES: dict[str, tuple[str, str]] = {
    "say": ("Samantha", "Daniel"),
    "espeak-ng": ("en-us", "en-gb"),
}


def _tts_command(engine: str, voice: str, text: str, out: Path) -> list[str]:
    if engine == "say":
        return ["say", "-v", voice, "-o", str(out), text]
    return ["espeak-ng", "-v", voice, "-w", str(out), text]


def _speak(engine: str, voice: str, text: str, workdir: Path) -> np.ndarray:
    """Return ``text`` spoken by ``voice`` as 16 kHz mono float32 samples."""
    out = workdir / ("turn.wav" if engine == "espeak-ng" else "turn.aiff")
    command = _tts_command(engine, voice, text, out)
    subprocess.run(command, check=True, capture_output=True)  # noqa: S603 — fixed TTS argv
    return np.asarray(decode_audio(str(out), sampling_rate=SAMPLE_RATE), dtype=np.float32)


def _write_wav(path: Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm.tobytes())


def build_session(
    turns: list[tuple[str, str]],
    engine: str,
    voices: tuple[str, str],
    bleed_db: float | None,
    out_dir: Path,
) -> float:
    """Synthesize one session into ``out_dir``. Returns its duration in seconds."""
    therapist_voice, client_voice = voices
    clips: list[tuple[str, str, np.ndarray]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for speaker, text in turns:
            voice = therapist_voice if speaker == "Therapist" else client_voice
            clips.append((speaker, text, _speak(engine, voice, text, Path(tmp))))

    lead = int(LEAD_IN_SECONDS * SAMPLE_RATE)
    gap = int(GAP_SECONDS * SAMPLE_RATE)
    total = lead + sum(len(c) + gap for _, _, c in clips)
    tracks = {"Therapist": np.zeros(total, np.float32), "Client": np.zeros(total, np.float32)}
    bleed = 0.0 if bleed_db is None else 10 ** (bleed_db / 20)

    reference: list[dict[str, object]] = []
    cursor = lead
    for speaker, text, clip in clips:
        other = "Client" if speaker == "Therapist" else "Therapist"
        tracks[speaker][cursor : cursor + len(clip)] += clip
        tracks[other][cursor : cursor + len(clip)] += clip * bleed
        reference.append(
            {
                "speaker": speaker,
                "start": round(cursor / SAMPLE_RATE, 3),
                "end": round((cursor + len(clip)) / SAMPLE_RATE, 3),
                "text": text,
            }
        )
        cursor += len(clip) + gap

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_wav(out_dir / "therapist.wav", tracks["Therapist"])
    _write_wav(out_dir / "client.wav", tracks["Client"])
    duration = total / SAMPLE_RATE
    (out_dir / "reference.json").write_text(
        json.dumps({"duration": round(duration, 3), "turns": reference}, indent=2) + "\n"
    )
    return duration


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path, help="directory to write the sessions into")
    parser.add_argument("--tts", choices=sorted(DEFAULT_VOICES), help="default: first found")
    parser.add_argument("--therapist-voice")
    parser.add_argument("--client-voice")
    parser.add_argument(
        "--bleed-db",
        type=float,
        default=DEFAULT_BLEED_DB,
        help=f"level of each voice on the other track, in dB (default {DEFAULT_BLEED_DB:g})",
    )
    parser.add_argument("--no-bleed", action="store_true", help="keep each track clean")
    args = parser.parse_args(argv)

    engine = args.tts or next((e for e in ("say", "espeak-ng") if shutil.which(e)), None)
    if engine is None or shutil.which(engine) is None:
        print("No text-to-speech engine found: install espeak-ng, or run on macOS (say).")
        return 1
    default_therapist, default_client = DEFAULT_VOICES[engine]
    voices = (args.therapist_voice or default_therapist, args.client_voice or default_client)
    bleed_db = None if args.no_bleed else args.bleed_db

    for name, turns in SESSIONS.items():
        duration = build_session(turns, engine, voices, bleed_db, args.out / name)
        print(f"{name}: {duration:.1f}s ({engine}, voices {voices[0]} / {voices[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
