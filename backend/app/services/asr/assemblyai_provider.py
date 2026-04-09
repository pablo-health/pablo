# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""AssemblyAI real-time streaming ASR provider (v3 Universal Streaming API)."""

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable

import websockets

from .base import AsrProvider

logger = logging.getLogger(__name__)

ASSEMBLYAI_RT_URL = "wss://streaming.assemblyai.com/v3/ws"

# v3 requires audio chunks between 50ms and 1000ms.
# At 16kHz mono 16-bit PCM: 100ms = 3200 bytes.
MIN_CHUNK_BYTES = 3200


class AssemblyAiAsrProvider(AsrProvider):
    """Real-time transcription via AssemblyAI Universal Streaming v3 API.

    Receives PCM 16kHz mono audio, buffers to meet the v3 minimum duration
    requirement (50ms), then sends binary audio frames over WebSocket.
    Fires on_transcript when turn transcripts arrive.
    """

    def __init__(
        self,
        api_key: str,
        on_transcript: Callable[[str, bool], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
        sample_rate: int = 16000,
    ) -> None:
        super().__init__(on_transcript=on_transcript, on_error=on_error)
        self._api_key = api_key
        self._sample_rate = sample_rate
        self._ws: websockets.WebSocketClientProtocol | None = None  # type: ignore[name-defined]
        self._receive_task: asyncio.Task[None] | None = None
        self._audio_buffer = bytearray()

    async def start(self) -> None:
        self._audio_buffer.clear()
        url = f"{ASSEMBLYAI_RT_URL}?sample_rate={self._sample_rate}&encoding=pcm_s16le"
        self._ws = await websockets.connect(  # type: ignore[attr-defined]
            url,
            additional_headers={"Authorization": self._api_key},
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("AssemblyAI ASR v3 stream started")

    async def send_audio(self, pcm_data: bytes) -> None:
        if not self._ws:
            return
        self._audio_buffer.extend(pcm_data)
        if len(self._audio_buffer) >= MIN_CHUNK_BYTES:
            chunk = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            try:
                await self._ws.send(chunk)
            except Exception as e:
                logger.error("AssemblyAI send error: %s", e)

    async def stop(self) -> None:
        # Flush remaining buffered audio
        if self._ws and self._audio_buffer:
            with contextlib.suppress(Exception):
                await self._ws.send(bytes(self._audio_buffer))
            self._audio_buffer.clear()

        if self._ws:
            with contextlib.suppress(Exception):
                await self._ws.send(json.dumps({"type": "Terminate"}))
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None

        logger.info("AssemblyAI ASR v3 stream stopped")

    async def _receive_loop(self) -> None:
        try:
            async for raw in self._ws:  # type: ignore[union-attr]
                msg = json.loads(raw)
                msg_type = msg.get("type", "")

                if msg_type == "Turn":
                    text = msg.get("transcript", "").strip()
                    is_final = msg.get("end_of_turn", False)
                    if text and is_final:
                        await self._on_transcript(text, True)

                elif msg_type == "Begin":
                    logger.info("AssemblyAI session began: %s", msg.get("id", ""))

                elif msg_type == "Termination":
                    break

                elif "error" in msg:
                    error_msg = msg.get("error", "Unknown ASR error")
                    logger.error("AssemblyAI error: %s", error_msg)
                    await self._on_error(str(error_msg))

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("AssemblyAI receive loop error: %s", e)
            await self._on_error(f"ASR connection lost: {e}")
