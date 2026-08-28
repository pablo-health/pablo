# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""AssemblyAI batch transcription service for session audio.

Replaces the GCP Batch + Whisper pipeline for managed deployments.
Submits audio to AssemblyAI's async transcription API, polls for completion,
and posts the merged transcript back to the internal callback endpoint.

Each channel is pre-processed with a simple energy-based VAD (similar to
Whisper's vad_filter), and the detected speech regions are concatenated into
a single speech-only file per channel, with an offset map recording where
each region sat in the original timeline. A session therefore submits
exactly one AssemblyAI job per channel no matter how long it ran or how
choppy the speech was; word timestamps are mapped back through the offset
map when results are merged. This keeps billable duration down, avoids the
long-silence recognition failures that motivated the VAD, and keeps the
submit/poll cost independent of session length.
"""

import bisect
import io
import logging
import wave
from collections.abc import Callable
from typing import Any

import httpx
import numpy as np
import numpy.typing as npt

from ..middleware.outbound import tracing_async_client
from ..settings import Settings

logger = logging.getLogger(__name__)

_JsonDict = dict[str, Any]

ASSEMBLYAI_API_BASE = "https://api.assemblyai.com/v2"
_SAMPLE_WIDTH_16BIT = 2
# Default PCM format from companion app (AudioCaptureKit)
_DEFAULT_PCM_SAMPLE_RATE = 48000
_DEFAULT_PCM_CHANNELS = 2
# VAD shape: loudness is reduced to one value per frame, regions closer than
# the merge gap collapse together, and each region keeps a little padding so
# soft leading/trailing phonemes survive the cut.
_VAD_FRAME_SECONDS = 0.01
_REGION_PAD_SECONDS = 0.15
_REGION_MERGE_GAP_SECONDS = 0.5
# Silence inserted between concatenated regions so words can't bleed across
# a region boundary in the transcription.
_CONCAT_GAP_SECONDS = 0.5


def _ensure_wav(audio_data: bytes, n_channels: int = _DEFAULT_PCM_CHANNELS) -> bytes:
    """Wrap raw PCM in a WAV header if needed.

    Older companion builds send raw headerless PCM, so the format has to be
    supplied out of band -- and it is not the same for both parts: the therapist
    sidecar is mono, the client sidecar is stereo. Guessing stereo for a mono part
    halves its frame count and interleaves adjacent samples into "channels",
    mangling the waveform into something that transcribes to nothing. Callers must
    therefore pass the channel count of the part they hold rather than rely on the
    default.

    Current builds send self-describing WAV and return at the RIFF check below,
    never reaching the guess.

    Multi-channel input is downmixed to mono: each part carries one speaker, so
    the channels are duplicates or a stereo image of the same voice, not separate
    people.
    """
    # Already a WAV? Return as-is -- the payload describes itself, so don't guess.
    if audio_data[:4] == b"RIFF":
        return audio_data

    if n_channels < 1:
        raise ValueError(f"n_channels must be >= 1, got {n_channels}")

    # Trim to whole frames
    frame_size = n_channels * _SAMPLE_WIDTH_16BIT
    n_frames = len(audio_data) // frame_size
    trimmed = audio_data[: n_frames * frame_size]

    if n_channels == 1:
        mono_bytes = trimmed
    else:
        channels = np.frombuffer(trimmed, dtype="<i2").reshape(-1, n_channels)
        # int32 sum before the divide: full-scale int16 samples overflow.
        mono = (channels.astype(np.int32).sum(axis=1) // n_channels).astype("<i2")
        mono_bytes = mono.tobytes()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(_SAMPLE_WIDTH_16BIT)
        wf.setframerate(_DEFAULT_PCM_SAMPLE_RATE)
        wf.writeframes(mono_bytes)

    duration = n_frames / _DEFAULT_PCM_SAMPLE_RATE
    logger.info(
        "Wrapped raw PCM as WAV: %.1fs, %d frames, %dch→mono", duration, n_frames, n_channels
    )
    return buf.getvalue()


# ADTS AAC frame sync: a 12-bit syncword (0xFFF) followed by a zero layer
# field — byte 0 == 0xFF and (byte 1 & 0xF6) == 0xF0.
_ADTS_SYNC_BYTE0 = 0xFF
_ADTS_BYTE1_LAYER_MASK = 0xF6
_ADTS_BYTE1_LAYER_MATCH = 0xF0
_ADTS_MIN_BYTES = 2


def _is_adts_aac(audio_data: bytes) -> bool:
    """True if the payload begins with an ADTS AAC frame sync."""
    return (
        len(audio_data) >= _ADTS_MIN_BYTES
        and audio_data[0] == _ADTS_SYNC_BYTE0
        and (audio_data[1] & _ADTS_BYTE1_LAYER_MASK) == _ADTS_BYTE1_LAYER_MATCH
    )


def sniff_audio_container(audio_data: bytes) -> tuple[str, str]:
    """Best-effort ``(extension, mime_type)`` for a prepared audio payload.

    Names and content-types the object handed to AssemblyAI. AssemblyAI decodes
    by content regardless, but is fed accurate metadata anyway. Anything not
    recognized as a self-describing container is treated as WAV (raw PCM is
    wrapped to WAV before staging).
    """
    if audio_data[:4] == b"RIFF":
        return "wav", "audio/wav"
    if _is_adts_aac(audio_data):
        return "aac", "audio/aac"
    return "wav", "audio/wav"


def _prepare_whole_audio(
    audio_data: bytes, n_channels: int = _DEFAULT_PCM_CHANNELS
) -> tuple[bytes, list[list[float]]]:
    """Prepare a channel for whole-file submission (no VAD).

    Self-describing containers (WAV, ADTS AAC) are submitted as-is; legacy
    headerless raw PCM is wrapped to WAV. The offset map is the identity
    ``[[0.0, 0.0]]`` — the submitted timeline is the original timeline, so the
    poller's per-word timestamp remap is a no-op.
    """
    if audio_data[:4] == b"RIFF" or _is_adts_aac(audio_data):
        return audio_data, [[0.0, 0.0]]
    return _ensure_wav(audio_data, n_channels), [[0.0, 0.0]]


# --- VAD: find and concatenate speech regions ---


def _merge_close_regions(regions: list[tuple[int, int]], gap_samples: int) -> list[tuple[int, int]]:
    """Merge adjacent regions that are closer than gap_samples apart."""
    merged: list[tuple[int, int]] = [regions[0]]
    for start, end in regions[1:]:
        if start - merged[-1][1] < gap_samples:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _speech_intervals(
    samples: npt.NDArray[np.int16],
    sample_rate: int,
    threshold: int,
    min_silence_ms: int,
) -> list[tuple[int, int]]:
    """Find (start, end) sample intervals that contain speech.

    Vectorized energy VAD: the signal is reduced to a coarse per-frame
    loudness envelope, then speech runs are the stretches of loud frames
    separated by more than the silence window. A frame is loud iff any
    sample in it clears the threshold, so boundaries are conservative to
    within one frame — well inside the region padding.
    """
    n = int(samples.size)
    if n == 0:
        return []

    frame = max(1, int(sample_rate * _VAD_FRAME_SECONDS))
    n_frames = -(-n // frame)  # ceil: the tail becomes a final short frame
    # int32 working copy: abs(-32768) overflows int16.
    magnitudes = np.zeros(n_frames * frame, dtype=np.int32)
    magnitudes[:n] = samples
    np.abs(magnitudes, out=magnitudes)
    envelope = magnitudes.reshape(n_frames, frame).max(axis=1)

    loud = np.flatnonzero(envelope > threshold)
    if loud.size == 0:
        return []

    min_silence_samples = sample_rate * min_silence_ms // 1000
    pad_samples = int(sample_rate * _REGION_PAD_SECONDS)

    # A region break wherever the silent stretch between consecutive loud
    # frames exceeds the window — the vectorized form of counting silent
    # samples until the count clears min_silence.
    breaks = np.flatnonzero((np.diff(loud) - 1) * frame > min_silence_samples)
    starts = loud[np.concatenate(([0], breaks + 1))] * frame
    ends = (loud[np.concatenate((breaks, [loud.size - 1]))] + 1) * frame

    # Regions extend into the silence that closed them, plus padding on both
    # sides; the final region is clamped to the end of the signal.
    starts = np.maximum(starts - pad_samples, 0)
    ends = np.minimum(ends + min_silence_samples + pad_samples, n)

    return _merge_close_regions(
        list(zip(starts.tolist(), ends.tolist(), strict=True)),
        int(sample_rate * _REGION_MERGE_GAP_SECONDS),
    )


def _prepare_speech_audio(
    audio_data: bytes,
    threshold: int = 500,
    min_silence_ms: int = 500,
    *,
    n_channels: int = _DEFAULT_PCM_CHANNELS,
) -> tuple[bytes, list[list[float]]]:
    """Reduce one channel to a single speech-only WAV plus an offset map.

    Runs the VAD, then concatenates the speech regions — separated by short
    silence gaps so words can't bleed across a boundary — into one WAV. The
    offset map records, per region, where it starts in the concatenated file
    and where it started in the original recording:
    ``[[concat_start_sec, original_start_sec], ...]``. Word timestamps from
    the transcription are mapped back through it in ``parse_result``.

    Audio the VAD can't analyze (not 16-bit mono, corrupt, or all silence)
    passes through whole with an identity map. ``n_channels`` describes a
    headerless raw-PCM payload's layout — see :func:`_ensure_wav`.
    """
    wav_data = _ensure_wav(audio_data, n_channels)
    identity: tuple[bytes, list[list[float]]] = (wav_data, [[0.0, 0.0]])
    try:
        with wave.open(io.BytesIO(wav_data), "rb") as wf:
            sample_rate = wf.getframerate()
            if wf.getsampwidth() != _SAMPLE_WIDTH_16BIT or wf.getnchannels() != 1:
                return identity
            raw = wf.readframes(wf.getnframes())
    except Exception:
        return identity

    samples: npt.NDArray[np.int16] = np.frombuffer(raw, dtype="<i2")
    intervals = _speech_intervals(samples, sample_rate, threshold, min_silence_ms)
    if not intervals:
        return identity

    gap = np.zeros(int(sample_rate * _CONCAT_GAP_SECONDS), dtype="<i2")
    pieces: list[npt.NDArray[np.int16]] = []
    offset_map: list[list[float]] = []
    concat_pos = 0
    for start, end in intervals:
        if pieces:
            pieces.append(gap)
            concat_pos += gap.size
        offset_map.append([concat_pos / sample_rate, start / sample_rate])
        pieces.append(samples[start:end])
        concat_pos += end - start

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(_SAMPLE_WIDTH_16BIT)
        wf.setframerate(sample_rate)
        wf.writeframes(np.concatenate(pieces).tobytes())

    original_duration = samples.size / sample_rate
    speech_duration = concat_pos / sample_rate
    logger.info(
        "VAD: %.1fs audio → %d regions concatenated to %.1fs (%.0f%% trimmed)",
        original_duration,
        len(intervals),
        speech_duration,
        (1 - speech_duration / original_duration) * 100 if original_duration else 0.0,
    )
    return buf.getvalue(), offset_map


# --- AssemblyAI service ---


class AssemblyAiTranscriptionService:
    """Batch transcription via AssemblyAI's async API.

    Two-phase flow for Cloud Tasks resilience:
    1. Submit: VAD → concatenate speech per channel → one job per channel
    2. Poll:   check each transcript_id → remap timestamps + merge when done

    The prepared audio reaches AssemblyAI either through its /upload
    endpoint or — when the caller passes ``audio_url_factory`` — via a URL
    AssemblyAI fetches itself (e.g. a presigned object-storage GET), which
    keeps the upload bandwidth out of this process.
    """

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.assemblyai_api_key.get_secret_value()
        self._speech_model = settings.assemblyai_speech_model
        self._vad_enabled = settings.assemblyai_vad_enabled
        self._speaker_labels_channels = set(settings.assemblyai_speaker_labels_channels)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._api_key}

    async def _upload_audio(self, client: httpx.AsyncClient, audio_data: bytes) -> str:
        response = await client.post(
            f"{ASSEMBLYAI_API_BASE}/upload",
            headers={**self._headers(), "Content-Type": "application/octet-stream"},
            content=audio_data,
            timeout=300,
        )
        response.raise_for_status()
        return response.json()["upload_url"]  # type: ignore[no-any-return]

    async def _submit_transcription(
        self, client: httpx.AsyncClient, audio_url: str, *, speaker_labels: bool
    ) -> str:
        body: _JsonDict = {
            "audio_url": audio_url,
            "language_code": "en",
            "speech_model": self._speech_model,
        }
        if speaker_labels:
            body["speaker_labels"] = True
        response = await client.post(
            f"{ASSEMBLYAI_API_BASE}/transcript",
            headers=self._headers(),
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("Submitted AssemblyAI job: id=%s", data["id"])
        return data["id"]  # type: ignore[no-any-return]

    async def submit_dual_channel(
        self,
        therapist_audio: bytes,
        client_audio: bytes,
        *,
        audio_url_factory: Callable[[str, bytes], str] | None = None,
    ) -> list[_JsonDict]:
        """Prepare and submit both channels — one AssemblyAI job each.

        ``audio_url_factory(speaker, wav_bytes)`` returns a URL AssemblyAI
        can fetch the prepared speech-only audio from; when omitted the
        bytes are pushed through AssemblyAI's /upload endpoint. Returns job
        metadata ``[{transcript_id, speaker, offset_map, diarized}, ...]``
        for the polling Cloud Task.
        """
        jobs: list[_JsonDict] = []
        async with tracing_async_client() as client:
            # The two parts have different layouts: the companion captures the
            # therapist from the mic as mono and the client from system
            # loopback as stereo. Only headerless payloads from older builds
            # depend on the channel count.
            channels = (
                ("Therapist", therapist_audio, 1),
                ("Client", client_audio, 2),
            )
            for speaker, audio, n_channels in channels:
                # VAD needs decodable PCM samples; compressed uploads (AAC) are
                # always submitted whole. So the speech-only path runs only when
                # VAD is enabled AND the payload isn't already a compressed
                # container — otherwise the whole file goes with an identity map.
                if self._vad_enabled and not _is_adts_aac(audio):
                    prepared, offset_map = _prepare_speech_audio(audio, n_channels=n_channels)
                else:
                    prepared, offset_map = _prepare_whole_audio(audio, n_channels=n_channels)
                if audio_url_factory is not None:
                    audio_url = audio_url_factory(speaker, prepared)
                else:
                    audio_url = await self._upload_audio(client, prepared)
                diarized = speaker in self._speaker_labels_channels
                transcript_id = await self._submit_transcription(
                    client, audio_url, speaker_labels=diarized
                )
                jobs.append(
                    {
                        "transcript_id": transcript_id,
                        "speaker": speaker,
                        "offset_map": offset_map,
                        "diarized": diarized,
                    }
                )

        logger.info("Submitted %d AssemblyAI jobs (one per channel)", len(jobs))
        return jobs

    @staticmethod
    def shift_job_offsets(jobs: list[_JsonDict], offset_seconds: float) -> list[_JsonDict]:
        """Shift a segment's ``offset_map`` onto the whole-session timeline.

        ``submit_dual_channel`` maps a segment's concatenated (VAD-trimmed)
        timeline back to that segment's own original timeline, starting at 0.
        Adding the segment's start-of-session offset to each entry's
        original-timeline value lets ``parse_result`` map every word straight
        to session-wide time with no further change.
        """
        if not offset_seconds:
            return jobs
        for job in jobs:
            offset_map = job.get("offset_map")
            if offset_map:
                job["offset_map"] = [
                    [concat_start, original_start + offset_seconds]
                    for concat_start, original_start in offset_map
                ]
        return jobs

    @staticmethod
    def check_job_status(api_key: str, transcript_id: str) -> tuple[str, _JsonDict | None]:
        """Check the status of a single AssemblyAI transcript (synchronous).

        Returns (status, result_data) where status is "completed", "error",
        or "processing". result_data is the full response when completed.
        """
        import httpx as httpx_sync

        url = f"{ASSEMBLYAI_API_BASE}/transcript/{transcript_id}"
        response = httpx_sync.get(url, headers={"Authorization": api_key}, timeout=30)
        response.raise_for_status()
        data = response.json()
        if data["status"] == "completed":
            return ("completed", data)
        if data["status"] == "error":
            return ("error", data)
        return ("processing", None)

    @staticmethod
    def parse_result(job_meta: _JsonDict, result: _JsonDict) -> list[_JsonDict]:
        """Turn one job's AssemblyAI result into utterances on the original timeline.

        Concatenated jobs carry an ``offset_map`` and each word's timestamp
        is mapped back through it; legacy per-region jobs carry a single
        constant ``original_offset``.
        """
        speaker: str = job_meta["speaker"]
        diarized = bool(job_meta.get("diarized"))
        offset_map: list[list[float]] | None = job_meta.get("offset_map")
        if offset_map:
            concat_starts = [entry[0] for entry in offset_map]

            def to_original(seconds: float) -> float:
                i = max(bisect.bisect_right(concat_starts, seconds) - 1, 0)
                concat_start, original_start = offset_map[i]
                return original_start + (seconds - concat_start)
        else:
            base = float(job_meta.get("original_offset", 0.0))

            def to_original(seconds: float) -> float:
                return base + seconds

        words = result.get("words", [])
        if not words:
            text = (result.get("text") or "").strip()
            if not text:
                return []
            start = to_original(0.0)
            return [{"start": start, "end": start, "speaker": speaker, "text": text}]

        def word_speaker(w: _JsonDict) -> str:
            if diarized and w.get("speaker"):
                return f"{speaker} {w['speaker']}"
            return speaker

        return _words_to_utterances(
            [
                {
                    "start": to_original(w["start"] / 1000),
                    "end": to_original(w["end"] / 1000),
                    "speaker": word_speaker(w),
                    "text": w["text"],
                }
                for w in words
            ],
            speaker,
        )

    @staticmethod
    def merge_utterances(jobs: list[_JsonDict]) -> str:
        """Merge per-job utterance lists into the canonical transcript text.

        ``jobs`` is the persisted job metadata after the poller attached each
        completed job's parsed utterances (see ``parse_result``).
        """
        return _merge_segments([u for job in jobs for u in job.get("utterances", [])])


# --- Utilities ---


def _words_to_utterances(
    word_segments: list[_JsonDict], speaker: str, gap_threshold: float = 1.5
) -> list[_JsonDict]:
    """Group word-level segments into utterances based on time gaps and speaker changes.

    Each word's own ``speaker`` field wins when present (diarized jobs carry a
    per-word label like "Client A"); words without one fall back to the
    channel-level ``speaker`` argument.
    """
    if not word_segments:
        return []

    def word_speaker(word: _JsonDict) -> str:
        speaker_value: str = word.get("speaker", speaker)
        return speaker_value

    utterances: list[_JsonDict] = []
    current_start = word_segments[0]["start"]
    current_end = word_segments[0]["end"]
    current_speaker = word_speaker(word_segments[0])
    current_words = [word_segments[0]["text"]]

    for word in word_segments[1:]:
        this_speaker = word_speaker(word)
        if word["start"] - current_end > gap_threshold or this_speaker != current_speaker:
            utterances.append(
                {
                    "start": current_start,
                    "end": current_end,
                    "speaker": current_speaker,
                    "text": " ".join(current_words),
                }
            )
            current_start = word["start"]
            current_words = []
            current_speaker = this_speaker

        current_end = word["end"]
        current_words.append(word["text"])

    utterances.append(
        {
            "start": current_start,
            "end": current_end,
            "speaker": current_speaker,
            "text": " ".join(current_words),
        }
    )
    return utterances


def _merge_segments(*channel_segments: list[_JsonDict]) -> str:
    """Merge segments from multiple channels, sorted by start time."""
    all_segments: list[_JsonDict] = []
    for segments in channel_segments:
        all_segments.extend(segments)

    all_segments.sort(key=lambda s: s["start"])

    lines: list[str] = []
    for seg in all_segments:
        start = _format_timestamp(float(seg["start"]))
        lines.append(f"[{start}]")
        lines.append(f"{seg['speaker']}: {seg['text']}")

    return "\n".join(lines)


def _format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
