# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Turn a short dictated clip into text.

A dictation is one speaker and a few minutes at most, so it skips everything
the session path does for two channels and long recordings: no voice-activity
split, no speaker labels, no polling across queue deliveries. The worker
submits the clip and waits for the words.

The provider is the deployment's transcription provider (AssemblyAI, under the
same agreement as session audio). The end-to-end stack has no provider; its
stand-in answers instead (``dictation_transcription_base_url``, refused
outside development). A deployment with neither offers no dictation.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Protocol

import httpx

from .assemblyai_transcription_service import ASSEMBLYAI_API_BASE, AssemblyAiTranscriptionService

if TYPE_CHECKING:
    from ..settings import Settings

logger = logging.getLogger(__name__)

_POLL_SECONDS = 3.0
#: A clip is at most ten minutes; a provider that hasn't answered by now is
#: retried by the queue rather than waited on.
_MAX_WAIT_SECONDS = 300.0


class DictationTranscriptionError(Exception):
    """The clip could not be transcribed, and trying again won't change that."""


class TransientDictationTranscriptionError(Exception):
    """The provider didn't answer this time; the queue should try again."""


class DictationTranscriber(Protocol):
    def transcribe(self, audio: bytes, content_type: str) -> str:
        """The clip's words.

        Raises:
            DictationTranscriptionError: the provider refused the clip.
            TransientDictationTranscriptionError: try again later.
        """


class AssemblyAiDictationTranscriber:
    def __init__(self, api_key: str, speech_model: str) -> None:
        self._api_key = api_key
        self._speech_model = speech_model

    def transcribe(self, audio: bytes, content_type: str) -> str:  # noqa: ARG002 — the provider sniffs the container itself
        headers = {"Authorization": self._api_key}
        try:
            uploaded = httpx.post(
                f"{ASSEMBLYAI_API_BASE}/upload",
                headers={**headers, "Content-Type": "application/octet-stream"},
                content=audio,
                timeout=120,
            )
            uploaded.raise_for_status()
            submitted = httpx.post(
                f"{ASSEMBLYAI_API_BASE}/transcript",
                headers=headers,
                json={
                    "audio_url": uploaded.json()["upload_url"],
                    "language_code": "en",
                    "speech_model": self._speech_model,
                },
                timeout=30,
            )
            submitted.raise_for_status()
        except httpx.HTTPError as exc:
            raise TransientDictationTranscriptionError(str(exc)) from exc
        transcript_id: str = submitted.json()["id"]
        try:
            return self._wait_for(transcript_id)
        finally:
            # The words now live on the dictation; nothing stays with the provider.
            try:
                AssemblyAiTranscriptionService.delete_transcript(self._api_key, transcript_id)
            except httpx.HTTPError:
                logger.warning("Could not delete dictation transcript %s", transcript_id)

    def _wait_for(self, transcript_id: str) -> str:
        deadline = time.monotonic() + _MAX_WAIT_SECONDS
        while True:
            try:
                status, data = AssemblyAiTranscriptionService.check_job_status(
                    self._api_key, transcript_id
                )
            except httpx.HTTPError as exc:
                raise TransientDictationTranscriptionError(str(exc)) from exc
            if status == "completed" and data is not None:
                return str(data.get("text") or "").strip()
            if status == "error":
                raise DictationTranscriptionError(f"Transcript {transcript_id} failed")
            if time.monotonic() > deadline:
                raise TransientDictationTranscriptionError(f"Transcript {transcript_id} pending")
            time.sleep(_POLL_SECONDS)


class HttpDictationTranscriber:
    """The end-to-end stack's stand-in: ``POST {base}/v1/transcribe`` → ``{"text"}``."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def transcribe(self, audio: bytes, content_type: str) -> str:
        try:
            response = httpx.post(
                f"{self._base_url}/v1/transcribe",
                content=audio,
                headers={"Content-Type": content_type},
                timeout=30,
            )
        except httpx.HTTPError as exc:
            raise TransientDictationTranscriptionError(str(exc)) from exc
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise TransientDictationTranscriptionError(f"stand-in {response.status_code}")
        if response.is_error:
            raise DictationTranscriptionError(f"stand-in {response.status_code}")
        return str(response.json().get("text") or "").strip()


def get_dictation_transcriber(settings: Settings) -> DictationTranscriber | None:
    """The deployment's transcriber for dictation, or ``None`` when it has none."""
    if settings.dictation_transcription_base_url:
        return HttpDictationTranscriber(settings.dictation_transcription_base_url)
    api_key = settings.assemblyai_api_key.get_secret_value()
    if (
        settings.transcription_enabled
        and settings.transcription_provider == "assemblyai"
        and api_key
    ):
        return AssemblyAiDictationTranscriber(api_key, settings.assemblyai_speech_model)
    return None
