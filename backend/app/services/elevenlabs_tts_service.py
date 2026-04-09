# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""ElevenLabs streaming TTS service for Practice Mode.

Converts text responses from Gemini into PCM audio using
ElevenLabs' streaming text-to-speech API.
"""

import logging
from collections.abc import Awaitable, Callable

import httpx

from ..settings import Settings

logger = logging.getLogger(__name__)

ELEVENLABS_API_BASE = "https://api.elevenlabs.io/v1"
HTTP_OK = 200


class ElevenLabsTTSError(Exception):
    """Error communicating with ElevenLabs API."""


class ElevenLabsTTSService:
    """Streaming text-to-speech via ElevenLabs REST API.

    Uses the streaming endpoint to minimize time-to-first-audio.
    Output format: PCM 16-bit signed LE, 24kHz, mono.
    """

    def __init__(self, settings: Settings, voice_id: str | None = None) -> None:
        self._api_key = settings.elevenlabs_api_key.get_secret_value()
        self._voice_id = voice_id or settings.elevenlabs_voice_id
        self._model_id = settings.elevenlabs_model_id
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0),
            )
        return self._client

    async def synthesize_stream(
        self,
        text: str,
        on_audio_chunk: Callable[[bytes, bool], Awaitable[None]],
    ) -> None:
        """Stream TTS for a text response.

        Calls ElevenLabs streaming endpoint and yields PCM audio chunks
        via the on_audio_chunk callback.

        Args:
            text: The text to synthesize.
            on_audio_chunk: Callback receiving (pcm_data, is_final).
        """
        if not text.strip():
            return

        client = await self._ensure_client()
        url = (
            f"{ELEVENLABS_API_BASE}/text-to-speech"
            f"/{self._voice_id}/stream?output_format=pcm_24000"
        )

        try:
            async with client.stream(
                "POST",
                url,
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": self._model_id,
                    "voice_settings": {
                        "stability": 0.5,
                        "similarity_boost": 0.75,
                    },
                },
            ) as response:
                if response.status_code != HTTP_OK:
                    body = await response.aread()
                    logger.error(
                        "ElevenLabs TTS error %d: %s",
                        response.status_code,
                        body[:500],
                    )
                    raise ElevenLabsTTSError(f"ElevenLabs returned {response.status_code}")

                chunk_count = 0
                async for chunk in response.aiter_bytes(chunk_size=4800):
                    # 4800 bytes = 100ms of PCM at 24kHz (24000 * 2 bytes * 0.1s)
                    if chunk:
                        chunk_count += 1
                        await on_audio_chunk(chunk, False)

                # Signal final chunk
                if chunk_count > 0:
                    await on_audio_chunk(b"", True)

        except httpx.HTTPError as e:
            logger.error("ElevenLabs HTTP error: %s", e)
            raise ElevenLabsTTSError(str(e)) from e

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
