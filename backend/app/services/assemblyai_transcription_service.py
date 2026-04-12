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

import asyncio
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

    Flow: VAD split → upload regions → transcribe each → offset timestamps → merge → callback.
    """

    def __init__(self, settings: Settings) -> None:
        self._api_key = settings.assemblyai_api_key.get_secret_value()
        self._backend_url = settings.transcription_backend_callback_url

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

    async def _poll_until_complete(
        self, client: httpx.AsyncClient, transcript_id: str
    ) -> _JsonDict:
        url = f"{ASSEMBLYAI_API_BASE}/transcript/{transcript_id}"
        elapsed = 0.0
        while elapsed < _POLL_TIMEOUT_SECONDS:
            response = await client.get(url, headers=self._headers(), timeout=30)
            response.raise_for_status()
            data = response.json()
            if data["status"] == "completed":
                return data  # type: ignore[no-any-return]
            if data["status"] == "error":
                raise RuntimeError(f"AssemblyAI failed: {data.get('error', 'unknown')}")
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            elapsed += _POLL_INTERVAL_SECONDS
        raise TimeoutError(f"AssemblyAI timed out after {_POLL_TIMEOUT_SECONDS}s")

    async def _transcribe_region(
        self,
        client: httpx.AsyncClient,
        region: _SpeechRegion,
        speaker: str,
    ) -> list[_JsonDict]:
        """Transcribe a single speech region and offset timestamps to original positions."""
        upload_url = await self._upload_audio(client, region.wav_data)
        transcript_id = await self._submit_transcription(client, upload_url)
        result = await self._poll_until_complete(client, transcript_id)

        words = result.get("words", [])
        if not words:
            text = result.get("text", "").strip()
            if text:
                return [
                    {
                        "start": region.original_offset,
                        "end": region.original_offset,
                        "speaker": speaker,
                        "text": text,
                    }
                ]
            return []

        return _words_to_utterances(
            [
                {
                    "start": region.original_offset + w["start"] / 1000,
                    "end": region.original_offset + w["end"] / 1000,
                    "speaker": speaker,
                    "text": w["text"],
                }
                for w in words
            ],
            speaker,
        )

    async def _transcribe_channel(
        self, client: httpx.AsyncClient, audio_data: bytes, speaker: str
    ) -> list[_JsonDict]:
        """Split channel into speech regions, transcribe each, combine results."""
        regions = _extract_speech_regions(audio_data)

        # Transcribe all regions concurrently
        tasks = [self._transcribe_region(client, region, speaker) for region in regions]
        results = await asyncio.gather(*tasks)

        all_utterances: list[_JsonDict] = []
        for utterances in results:
            all_utterances.extend(utterances)

        logger.info(
            "Channel %s: %d regions → %d utterances", speaker, len(regions), len(all_utterances)
        )
        return all_utterances

    async def transcribe_dual_channel(
        self,
        therapist_audio: bytes,
        client_audio: bytes,
        session_id: str,
        tenant_db: str,
        user_id: str,
    ) -> None:
        """Transcribe both channels in parallel, merge, and callback to backend."""
        try:
            async with httpx.AsyncClient() as client:
                therapist_task = self._transcribe_channel(client, therapist_audio, "Therapist")
                client_task = self._transcribe_channel(client, client_audio, "Client")
                therapist_segments, client_segments = await asyncio.gather(
                    therapist_task, client_task
                )

            transcript = _merge_segments(therapist_segments, client_segments)
            await self._callback_to_backend(session_id, tenant_db, user_id, transcript)
            logger.info("AssemblyAI transcription complete: session=%s", session_id)

        except Exception:
            logger.exception("AssemblyAI transcription failed: session=%s", session_id)

    async def transcribe_single_channel(
        self,
        audio_data: bytes,
        session_id: str,
        tenant_db: str,
        user_id: str,
    ) -> None:
        """Transcribe a single audio file and callback to backend."""
        try:
            async with httpx.AsyncClient() as client:
                segments = await self._transcribe_channel(client, audio_data, "Speaker")

            transcript = _merge_segments(segments)
            await self._callback_to_backend(session_id, tenant_db, user_id, transcript)
            logger.info("AssemblyAI transcription complete: session=%s", session_id)

        except Exception:
            logger.exception("AssemblyAI transcription failed: session=%s", session_id)

    async def _callback_to_backend(
        self,
        session_id: str,
        tenant_db: str,
        user_id: str,
        transcript: str,
    ) -> None:
        url = f"{self._backend_url}/api/internal/transcription-complete"
        headers: dict[str, str] = {}

        # In production, use IAM identity token for service-to-service auth
        try:
            import google.auth.transport.requests
            import google.oauth2.id_token

            auth_req = google.auth.transport.requests.Request()
            token = google.oauth2.id_token.fetch_id_token(auth_req, self._backend_url)
            headers["Authorization"] = f"Bearer {token}"
        except Exception:
            logger.debug("Could not fetch ID token for callback (dev mode?)")

        payload = {
            "session_id": session_id,
            "tenant_db": tenant_db,
            "user_id": user_id,
            "transcript_content": transcript,
            "transcript_format": "vtt",
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=300)
            response.raise_for_status()


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
