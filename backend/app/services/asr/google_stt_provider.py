# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Google Cloud Speech-to-Text v2 streaming ASR provider."""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from typing import TYPE_CHECKING

from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech

from .base import AsrProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Generator

    from ...settings import Settings

logger = logging.getLogger(__name__)


class GoogleSttAsrProvider(AsrProvider):
    """Real-time transcription via Google Cloud Speech-to-Text v2 streaming API.

    Runs the synchronous gRPC streaming call in a background thread and bridges
    results back to the async event loop via callbacks.
    """

    def __init__(
        self,
        settings: Settings,
        on_transcript: Callable[[str, bool], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
        sample_rate: int = 16000,
    ) -> None:
        super().__init__(on_transcript=on_transcript, on_error=on_error)
        self._project_id = settings.gcp_project_id
        self._location = settings.transcription_queue_location or "us-central1"
        self._sample_rate = sample_rate
        self._audio_queue: queue.Queue[bytes | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopped = False

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stopped = False
        self._thread = threading.Thread(target=self._stream_thread, daemon=True)
        self._thread.start()
        logger.info("Google STT stream started")

    async def send_audio(self, pcm_data: bytes) -> None:
        if not self._stopped:
            self._audio_queue.put(pcm_data)

    async def stop(self) -> None:
        self._stopped = True
        self._audio_queue.put(None)  # sentinel
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Google STT stream stopped")

    def _audio_generator(
        self,
    ) -> Generator[cloud_speech.StreamingRecognizeRequest, None, None]:
        """Yields audio chunks from the queue for the streaming request."""
        while True:
            chunk = self._audio_queue.get()
            if chunk is None:
                break
            yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

    def _stream_thread(self) -> None:
        """Runs synchronous gRPC streaming in a background thread."""
        try:
            client = SpeechClient()
            recognizer = f"projects/{self._project_id}/locations/{self._location}/recognizers/_"

            config = cloud_speech.StreamingRecognitionConfig(
                config=cloud_speech.RecognitionConfig(
                    explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                        encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                        sample_rate_hertz=self._sample_rate,
                        audio_channel_count=1,
                    ),
                    language_codes=["en-US"],
                    model="long",
                ),
                streaming_features=cloud_speech.StreamingRecognitionFeatures(
                    interim_results=False,
                    enable_voice_activity_events=True,
                ),
            )

            config_request = cloud_speech.StreamingRecognizeRequest(
                recognizer=recognizer,
                streaming_config=config,
            )

            def request_generator() -> Generator[
                cloud_speech.StreamingRecognizeRequest, None, None
            ]:
                yield config_request
                yield from self._audio_generator()

            responses = client.streaming_recognize(requests=request_generator())

            for response in responses:
                if self._stopped:
                    break
                for result in response.results:
                    if result.is_final and result.alternatives:
                        text = result.alternatives[0].transcript.strip()
                        if text and self._loop:
                            coro = self._on_transcript(text, True)
                            asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]

        except Exception as e:
            logger.error("Google STT stream error: %s", e)
            if self._loop and not self._stopped:
                coro = self._on_error(f"ASR error: {e}")
                asyncio.run_coroutine_threadsafe(coro, self._loop)  # type: ignore[arg-type]
