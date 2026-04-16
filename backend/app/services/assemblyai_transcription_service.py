# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""AssemblyAI batch transcription service for session audio.

Replaces the GCP Batch + Whisper pipeline for SaaS deployments.
Submits audio to AssemblyAI's async transcription API, polls for completion,
and posts the merged transcript back to the internal callback endpoint.

Each channel is pre-processed with a simple energy-based VAD (similar to
Whisper's vad_filter) that splits audio into individual speech regions.
Each region is transcribed independently, preserving the original timestamps.
This both reduces billable duration and ensures reliable recognition of
synthetic voices and accented speech that long-silence files can confuse.
"""

import io
import logging
import struct
import wave
from dataclasses import dataclass
from typing import Any

import httpx

from ..settings import Settings

logger = logging.getLogger(__name__)

_JsonDict = dict[str, Any]

ASSEMBLYAI_API_BASE = "https://api.assemblyai.com/v2"
_POLL_INTERVAL_SECONDS = 5
_POLL_TIMEOUT_SECONDS = 1800  # 30 min
_SAMPLE_WIDTH_16BIT = 2
# Default PCM format from companion app (AudioCaptureKit)
_DEFAULT_PCM_SAMPLE_RATE = 48000
_DEFAULT_PCM_CHANNELS = 2


def _ensure_wav(audio_data: bytes) -> bytes:
    """Wrap raw PCM in a WAV header if needed.

    The companion app sends raw 48kHz/16-bit/stereo PCM with no header.
    AssemblyAI and the VAD both need a recognizable WAV container.
    Stereo is downmixed to mono since each channel is a separate speaker.
    """
    # Already a WAV? Return as-is.
    if audio_data[:4] == b"RIFF":
        return audio_data

    # Raw PCM → mono WAV
    n_channels = _DEFAULT_PCM_CHANNELS
    bytes_per_sample = _SAMPLE_WIDTH_16BIT
    frame_size = n_channels * bytes_per_sample

    # Trim to whole frames
    n_frames = len(audio_data) // frame_size
    trimmed = audio_data[: n_frames * frame_size]

    # Downmix stereo to mono by averaging channels
    samples = struct.unpack(f"<{n_frames * n_channels}h", trimmed)
    mono = struct.pack(
        f"<{n_frames}h",
        *((samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples), n_channels)),
    )

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(bytes_per_sample)
        wf.setframerate(_DEFAULT_PCM_SAMPLE_RATE)
        wf.writeframes(mono)

    duration = n_frames / _DEFAULT_PCM_SAMPLE_RATE
    logger.info("Wrapped raw PCM as WAV: %.1fs, %d frames, stereo→mono", duration, n_frames)
    return buf.getvalue()


# --- VAD: split audio into speech regions ---


@dataclass
class _SpeechRegion:
    """A speech region extracted from audio with its original time offset."""

    wav_data: bytes
    original_offset: float  # seconds into the original audio where this region starts


def _merge_close_regions(regions: list[tuple[int, int]], gap_samples: int) -> list[tuple[int, int]]:
    """Merge adjacent regions that are closer than gap_samples apart."""
    merged: list[tuple[int, int]] = [regions[0]]
    for start, end in regions[1:]:
        if start - merged[-1][1] < gap_samples:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _extract_speech_regions(
    audio_data: bytes,
    threshold: int = 500,
    min_silence_ms: int = 500,
) -> list[_SpeechRegion]:
    """Extract individual speech regions from WAV audio using energy-based VAD.

    Returns a list of WAV chunks, each with its original time offset.
    If the audio isn't 16-bit mono WAV, returns a single region with the
    original audio (passthrough).
    """
    try:
        with wave.open(io.BytesIO(audio_data), "rb") as wf:
            sample_rate = wf.getframerate()
            if wf.getsampwidth() != _SAMPLE_WIDTH_16BIT or wf.getnchannels() != 1:
                return [_SpeechRegion(wav_data=audio_data, original_offset=0.0)]
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
    except Exception:
        return [_SpeechRegion(wav_data=audio_data, original_offset=0.0)]

    samples = struct.unpack(f"<{n_frames}h", raw)
    min_silence_samples = int(sample_rate * min_silence_ms / 1000)
    pad_samples = int(sample_rate * 0.15)

    # Find raw speech regions
    in_speech = False
    speech_start = 0
    silence_count = 0
    raw_regions: list[tuple[int, int]] = []

    for i, s in enumerate(samples):
        if abs(s) > threshold:
            silence_count = 0
            if not in_speech:
                in_speech = True
                speech_start = max(0, i - pad_samples)
        else:
            silence_count += 1
            if in_speech and silence_count > min_silence_samples:
                in_speech = False
                raw_regions.append((speech_start, min(i + pad_samples, n_frames)))

    if in_speech:
        raw_regions.append((speech_start, n_frames))

    if not raw_regions:
        return [_SpeechRegion(wav_data=audio_data, original_offset=0.0)]

    merged = _merge_close_regions(raw_regions, int(sample_rate * 0.5))

    # Convert each region to a standalone WAV
    regions: list[_SpeechRegion] = []
    original_duration = n_frames / sample_rate
    speech_duration = 0.0

    for start, end in merged:
        region_samples = samples[start:end]
        pcm = struct.pack(f"<{len(region_samples)}h", *region_samples)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(_SAMPLE_WIDTH_16BIT)
            wf.setframerate(sample_rate)
            wf.writeframes(pcm)
        offset = start / sample_rate
        regions.append(_SpeechRegion(wav_data=buf.getvalue(), original_offset=offset))
        speech_duration += (end - start) / sample_rate

    saved_pct = (1 - speech_duration / original_duration) * 100 if original_duration > 0 else 0
    logger.info(
        "VAD: %.1fs audio → %d regions (%.1fs speech, %.0f%% silence removed)",
        original_duration,
        len(regions),
        speech_duration,
        saved_pct,
    )
    return regions


# --- AssemblyAI service ---


class AssemblyAiTranscriptionService:
    """Batch transcription via AssemblyAI's async API.

    Two-phase flow for Cloud Tasks resilience:
    1. Submit: VAD split → upload regions → submit to AssemblyAI → return job metadata
    2. Poll:   Check each transcript_id → merge when all complete → process result

    The submit phase runs in the request context (fast, seconds).
    The poll phase runs as a Cloud Task with automatic retries.
    """

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.assemblyai_api_key.get_secret_value()

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

    async def _submit_transcription(self, client: httpx.AsyncClient, audio_url: str) -> str:
        response = await client.post(
            f"{ASSEMBLYAI_API_BASE}/transcript",
            headers=self._headers(),
            json={
                "audio_url": audio_url,
                "language_code": "en",
                "speech_model": "best",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        logger.info("Submitted AssemblyAI job: id=%s", data["id"])
        return data["id"]  # type: ignore[no-any-return]

    async def _submit_channel_regions(
        self, client: httpx.AsyncClient, audio_data: bytes, speaker: str
    ) -> list[_JsonDict]:
        """VAD split + upload + submit for one channel. Returns job metadata per region."""
        wav_data = _ensure_wav(audio_data)
        regions = _extract_speech_regions(wav_data)

        jobs: list[_JsonDict] = []
        for region in regions:
            upload_url = await self._upload_audio(client, region.wav_data)
            transcript_id = await self._submit_transcription(client, upload_url)
            jobs.append(
                {
                    "transcript_id": transcript_id,
                    "speaker": speaker,
                    "original_offset": region.original_offset,
                }
            )

        logger.info("Channel %s: submitted %d regions to AssemblyAI", speaker, len(jobs))
        return jobs

    async def submit_dual_channel(
        self,
        therapist_audio: bytes,
        client_audio: bytes,
    ) -> list[_JsonDict]:
        """Upload and submit both channels. Returns job metadata for Cloud Task polling.

        This is the fast phase — runs in the request context. Returns a list of
        dicts with {transcript_id, speaker, original_offset} for each region.
        """
        async with httpx.AsyncClient() as client:
            therapist_jobs = await self._submit_channel_regions(
                client, therapist_audio, "Therapist"
            )
            client_jobs = await self._submit_channel_regions(client, client_audio, "Client")

        all_jobs = therapist_jobs + client_jobs
        logger.info("Submitted %d AssemblyAI jobs for dual-channel transcription", len(all_jobs))
        return all_jobs

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
    def process_completed_jobs(jobs_with_results: list[tuple[_JsonDict, _JsonDict]]) -> str:
        """Merge completed AssemblyAI results into a VTT-format transcript.

        Args:
            jobs_with_results: list of (job_metadata, assemblyai_result) tuples.
        """
        all_utterances: list[_JsonDict] = []
        for job_meta, result in jobs_with_results:
            speaker = job_meta["speaker"]
            offset = job_meta["original_offset"]
            words = result.get("words", [])
            if not words:
                text = result.get("text", "").strip()
                if text:
                    all_utterances.append(
                        {
                            "start": offset,
                            "end": offset,
                            "speaker": speaker,
                            "text": text,
                        }
                    )
                continue
            all_utterances.extend(
                _words_to_utterances(
                    [
                        {
                            "start": offset + w["start"] / 1000,
                            "end": offset + w["end"] / 1000,
                            "speaker": speaker,
                            "text": w["text"],
                        }
                        for w in words
                    ],
                    speaker,
                )
            )
        return _merge_segments(all_utterances)


# --- Utilities ---


def _words_to_utterances(
    word_segments: list[_JsonDict], speaker: str, gap_threshold: float = 1.5
) -> list[_JsonDict]:
    """Group word-level segments into utterances based on time gaps."""
    if not word_segments:
        return []

    utterances: list[_JsonDict] = []
    current_start = word_segments[0]["start"]
    current_end = word_segments[0]["end"]
    current_words = [word_segments[0]["text"]]

    for word in word_segments[1:]:
        if word["start"] - current_end > gap_threshold:
            utterances.append(
                {
                    "start": current_start,
                    "end": current_end,
                    "speaker": speaker,
                    "text": " ".join(current_words),
                }
            )
            current_start = word["start"]
            current_words = []

        current_end = word["end"]
        current_words.append(word["text"])

    utterances.append(
        {
            "start": current_start,
            "end": current_end,
            "speaker": speaker,
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
