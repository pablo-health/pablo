# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Transcription worker — downloads audio from GCS, runs Whisper, callbacks to backend.

Supports dual-channel audio (therapist mic + client system audio) matching
the companion app's AudioCaptureKit channel split. Each channel is transcribed
separately with speaker labels, then merged chronologically by timestamp.
"""

import logging
import signal
import tempfile
from dataclasses import dataclass
from pathlib import Path

import google.auth.transport.requests
import google.oauth2.id_token
import httpx
from faster_whisper import WhisperModel
from google.cloud import storage

from config import TranscriptionSettings

logger = logging.getLogger(__name__)

# Graceful shutdown on SIGTERM (spot preemption sends this)
_shutting_down = False


def _handle_sigterm(_signum: int, _frame: object) -> None:
    global _shutting_down  # noqa: PLW0603
    logger.warning("SIGTERM received — marking for graceful shutdown")
    _shutting_down = True


signal.signal(signal.SIGTERM, _handle_sigterm)


@dataclass
class LabeledSegment:
    """A transcript segment with speaker label and timestamps."""

    start: float
    end: float
    speaker: str
    text: str


class TranscriptionWorker:
    """Downloads audio from GCS, transcribes with faster-whisper, posts result to backend."""

    def __init__(self, settings: TranscriptionSettings) -> None:
        self.settings = settings
        self._model: WhisperModel | None = None
        self._gcs_client: storage.Client | None = None

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            device = self.settings.whisper_device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    device = "cpu"

            compute_type = self.settings.whisper_compute_type
            if device == "cpu" and compute_type == "float16":
                compute_type = "int8"

            logger.info(
                "Loading Whisper model=%s device=%s compute=%s",
                self.settings.whisper_model_size,
                device,
                compute_type,
            )
            self._model = WhisperModel(
                self.settings.whisper_model_size,
                device=device,
                compute_type=compute_type,
            )
        return self._model

    @property
    def gcs_client(self) -> storage.Client:
        if self._gcs_client is None:
            self._gcs_client = storage.Client()
        return self._gcs_client

    # Frozen tuple so the allowlist values are compile-time constants
    _ALLOWED_AUDIO_SUFFIXES = (".wav", ".mp3", ".mp4", ".ogg", ".webm", ".flac", ".m4a")

    @staticmethod
    def _safe_audio_suffix(gcs_path: str) -> str:
        """Return a hardcoded suffix from the allowlist matching the file extension.

        Extracts the extension from the final path segment, then looks it up
        in the allowlist. Returns the allowlist's own constant — never a value
        derived from user input — so taint analysis tools see a clean string.
        """
        filename = gcs_path.rsplit("/", 1)[-1]
        dot_pos = filename.rfind(".")
        candidate = filename[dot_pos:].lower() if dot_pos != -1 else ""
        for allowed in TranscriptionWorker._ALLOWED_AUDIO_SUFFIXES:
            if candidate == allowed:
                return allowed  # return the hardcoded constant, not candidate
        return ""

    def download_audio(self, gcs_path: str) -> Path:
        """Download audio from GCS to a temp file. Returns the local path."""
        if ".." in gcs_path or gcs_path.startswith("/"):
            raise ValueError(f"Invalid GCS path: {gcs_path!r}")

        suffix = self._safe_audio_suffix(gcs_path)
        if not suffix:
            raise ValueError(f"Unsupported audio format in path: {gcs_path!r}")

        bucket_name = self.settings.gcs_audio_bucket
        blob = self.gcs_client.bucket(bucket_name).blob(gcs_path)

        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        tmp_path = tmp.name
        tmp.close()
        blob.download_to_filename(tmp_path)
        logger.info("Downloaded gs://%s/%s → %s", bucket_name, gcs_path, tmp_path)
        return Path(tmp_path)

    def transcribe_channel(self, audio_path: Path, speaker: str) -> list[LabeledSegment]:
        """Run Whisper on a single audio channel. Returns labeled segments."""
        if _shutting_down:
            raise RuntimeError("Shutting down — aborting transcription")

        logger.info("Transcribing %s (speaker: %s)", audio_path, speaker)
        segments, info = self.model.transcribe(
            str(audio_path),
            language="en",
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )

        logger.info(
            "Detected language=%s probability=%.2f duration=%.1fs",
            info.language,
            info.language_probability,
            info.duration,
        )

        labeled: list[LabeledSegment] = []
        for segment in segments:
            if _shutting_down:
                raise RuntimeError("Shutting down — aborting transcription")

            text = segment.text.strip()
            if text:
                labeled.append(LabeledSegment(
                    start=segment.start,
                    end=segment.end,
                    speaker=speaker,
                    text=text,
                ))

        logger.info("Channel %s: %d segments", speaker, len(labeled))
        return labeled

    def merge_channels(self, *channel_segments: list[LabeledSegment]) -> str:
        """Merge segments from multiple channels, sorted by start time.

        Produces a transcript with speaker labels and timestamps:
            [00:01:23 --> 00:01:45] Therapist: How are you feeling today?
            [00:01:46 --> 00:02:10] Client: I've been anxious this week...
        """
        all_segments: list[LabeledSegment] = []
        for segments in channel_segments:
            all_segments.extend(segments)

        all_segments.sort(key=lambda s: s.start)

        lines: list[str] = []
        for seg in all_segments:
            start = _format_timestamp(seg.start)
            end = _format_timestamp(seg.end)
            lines.append(f"[{start} --> {end}] {seg.speaker}: {seg.text}")

        transcript = "\n".join(lines)
        logger.info("Merged transcript: %d segments, %d chars", len(lines), len(transcript))
        return transcript

    def callback_to_backend(
        self,
        session_id: str,
        tenant_db: str,
        user_id: str,
        transcript: str,
    ) -> None:
        """POST the transcript back to the Pablo backend's internal endpoint."""
        url = f"{self.settings.backend_url}/api/internal/transcription-complete"

        headers: dict[str, str] = {"Content-Type": "application/json"}

        # In production, use IAM identity token for service-to-service auth.
        # Always attempt token fetch unless explicitly in dev mode.
        if not self.settings.dev_mode:
            try:
                auth_req = google.auth.transport.requests.Request()
                token = google.oauth2.id_token.fetch_id_token(auth_req, self.settings.backend_url)
                headers["Authorization"] = f"Bearer {token}"
            except Exception:
                logger.warning("Could not fetch ID token — running without auth (dev mode?)")

        payload = {
            "session_id": session_id,
            "tenant_db": tenant_db,
            "user_id": user_id,
            "transcript_content": transcript,
            "transcript_format": "vtt",
        }

        response = httpx.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        logger.info("Callback succeeded for session %s: %s", session_id, response.status_code)

    def process_job(
        self,
        session_id: str,
        tenant_db: str,
        user_id: str,
        gcs_path: str,
    ) -> dict[str, str]:
        """Full pipeline: download → transcribe both channels → merge → callback.

        gcs_path is comma-separated: "therapist_path,client_path"
        """
        temp_files: list[Path] = []
        try:
            paths = gcs_path.split(",")
            if len(paths) == 2:
                therapist_path = self.download_audio(paths[0])
                client_path = self.download_audio(paths[1])
                temp_files.extend([therapist_path, client_path])

                therapist_segments = self.transcribe_channel(therapist_path, "Therapist")
                client_segments = self.transcribe_channel(client_path, "Client")
                transcript = self.merge_channels(therapist_segments, client_segments)
            else:
                # Single file fallback (no speaker labels)
                audio_path = self.download_audio(paths[0])
                temp_files.append(audio_path)
                segments = self.transcribe_channel(audio_path, "Speaker")
                transcript = self.merge_channels(segments)

            self.callback_to_backend(session_id, tenant_db, user_id, transcript)
            return {"status": "completed", "session_id": session_id}
        finally:
            for f in temp_files:
                if f.exists():
                    f.unlink()
                    logger.debug("Cleaned up temp file %s", f)


def _format_timestamp(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
