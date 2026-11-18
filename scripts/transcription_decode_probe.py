# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Prove the transcription worker's locked dependencies can decode audio.

``/health`` never loads the model or opens a file, so it passes on a lock in
which every transcription fails. That happened once: PyAV 19 rejects the
``metadata_errors`` argument faster-whisper passes to ``av.open``, and only a
real decode showed it. This probe runs that real path.

For each suffix in the worker's allowlist it encodes a short clip with the
locked PyAV, then:

1. decodes it with ``faster_whisper.decode_audio`` and checks the length, so
   a silent decode failure cannot pass; and
2. runs ``TranscriptionWorker.transcribe_channel`` on it, the call every
   transcription job makes, with the ``tiny`` model on CPU; and
3. transcribes it again with VAD off. A tone holds no speech, so the worker's
   VAD drops the whole clip and step 2 never reaches the encoder or decoder.
   This call does, and it consumes the segments, so a lock that breaks
   inference (``ctranslate2``, ``tokenizers``, ``numpy``) fails here.

Run it from an environment holding exactly the lock:

    python -m venv /tmp/tx && /tmp/tx/bin/pip install --require-hashes \\
        --only-binary :all: -r services/transcription/requirements.txt
    /tmp/tx/bin/python scripts/transcription_decode_probe.py

Exits non-zero, naming the failing formats, if any format fails.
"""

from __future__ import annotations

import sys
import tempfile
import traceback
from fractions import Fraction
from pathlib import Path

import numpy as np

WORKER_DIR = Path(__file__).resolve().parent.parent / "services" / "transcription"
sys.path.insert(0, str(WORKER_DIR))

import av  # noqa: E402
from config import TranscriptionSettings  # noqa: E402
from faster_whisper import decode_audio  # noqa: E402
from worker import TranscriptionWorker  # noqa: E402

SAMPLE_RATE = 16000
SECONDS = 3

# Suffix -> (container format, audio codec). Covers every entry in
# TranscriptionWorker._ALLOWED_AUDIO_SUFFIXES; the check below fails if the
# allowlist grows and this table does not.
FORMATS: dict[str, tuple[str, str]] = {
    ".wav": ("wav", "pcm_s16le"),
    ".flac": ("flac", "flac"),
    ".mp3": ("mp3", "libmp3lame"),
    ".m4a": ("ipod", "aac"),
    ".mp4": ("mp4", "aac"),
    ".ogg": ("ogg", "libopus"),
    ".webm": ("webm", "libopus"),
}


def _write_clip(path: Path, container_format: str, codec: str) -> None:
    """Encode SECONDS of a 440 Hz tone, mono, into ``path``."""
    t = np.arange(SAMPLE_RATE * SECONDS) / SAMPLE_RATE
    samples = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    # Opus encodes at 48 kHz only; every other codec here takes 16 kHz.
    rate = 48000 if codec == "libopus" else SAMPLE_RATE
    if rate != SAMPLE_RATE:
        samples = np.interp(
            np.arange(len(samples) * rate // SAMPLE_RATE) * SAMPLE_RATE / rate,
            np.arange(len(samples)),
            samples,
        ).astype(np.float32)

    with av.open(str(path), mode="w", format=container_format) as container:
        stream = container.add_stream(codec, rate=rate, layout="mono")
        frame_size = stream.codec_context.frame_size or 1024
        fmt = stream.codec_context.format.name
        resampler = av.AudioResampler(format=fmt, layout="mono", rate=rate)
        pts = 0
        for start in range(0, len(samples), frame_size):
            chunk = samples[start : start + frame_size]
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="flt", layout="mono")
            frame.sample_rate = rate
            frame.pts = pts
            frame.time_base = Fraction(1, rate)
            pts += len(chunk)
            for resampled in resampler.resample(frame):
                container.mux(stream.encode(resampled))
        container.mux(stream.encode(None))


def main() -> int:
    allowlist = set(TranscriptionWorker._ALLOWED_AUDIO_SUFFIXES)
    if allowlist != set(FORMATS):
        print(f"FORMATS does not match the worker allowlist: {sorted(allowlist ^ set(FORMATS))}")
        return 1

    print(f"av {av.__version__}")
    settings = TranscriptionSettings(
        whisper_model_size="tiny", whisper_device="cpu", whisper_compute_type="int8"
    )
    worker = TranscriptionWorker(settings)

    failed: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        for suffix, (container_format, codec) in FORMATS.items():
            path = Path(tmp) / f"probe{suffix}"
            try:
                _write_clip(path, container_format, codec)
                decoded = decode_audio(str(path), sampling_rate=SAMPLE_RATE)
                seconds = len(decoded) / SAMPLE_RATE
                if not SECONDS - 0.5 <= seconds <= SECONDS + 0.5:
                    raise AssertionError(f"decoded {seconds:.2f}s, expected about {SECONDS}s")
                worker.transcribe_channel(path, "probe")
                segments, _ = worker.model.transcribe(str(path), language="en", vad_filter=False)
                list(segments)
            except Exception:
                traceback.print_exc()
                failed.append(suffix)
                print(f"FAIL {suffix}")
            else:
                print(f"ok   {suffix} ({codec}, {seconds:.2f}s decoded)")

    if failed:
        print(f"Decode probe failed for: {' '.join(failed)}")
        return 1
    print("Decode probe passed for every allowlisted format.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
