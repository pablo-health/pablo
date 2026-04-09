# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Gemini Live API wrapper for Practice Mode.

Manages a real-time audio conversation session with Gemini.
Configured for TEXT output — audio input from the therapist is processed
by Gemini's built-in VAD and speech understanding, and text responses
are returned for downstream TTS (ElevenLabs).
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from google import genai
from google.genai import types as genai_types

from ..settings import Settings

logger = logging.getLogger(__name__)


class GeminiLiveError(Exception):
    """Error communicating with Gemini Live API."""


class GeminiLiveSession:
    """A single Gemini Live API session for real-time audio conversation.

    Receives therapist audio (PCM 16kHz) via send_audio().
    Returns text responses via the on_text_response callback.
    """

    def __init__(
        self,
        settings: Settings,
        system_prompt: str,
        on_text_response: Callable[[str, bool], Awaitable[None]],
        on_status_change: Callable[[str], Awaitable[None]],
        on_error: Callable[[str, bool], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._system_prompt = system_prompt
        self._on_text_response = on_text_response
        self._on_status_change = on_status_change
        self._on_error = on_error
        self._session: Any = None
        self._client: genai.Client | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._exit_stack: contextlib.AsyncExitStack | None = None

    async def connect(self) -> None:
        """Open the Gemini Live WebSocket connection."""
        self._client = genai.Client(
            vertexai=True,
            project=self._settings.gcp_project_id,
            location=self._settings.transcription_queue_location or "us-central1",
        )

        config = genai_types.LiveConnectConfig(
            response_modalities=["TEXT"],  # type: ignore[list-item]
            system_instruction=genai_types.Content(
                parts=[genai_types.Part(text=self._system_prompt)]
            ),
        )

        self._exit_stack = contextlib.AsyncExitStack()
        self._session = await self._exit_stack.enter_async_context(
            self._client.aio.live.connect(  # type: ignore[misc]
                model=self._settings.practice_gemini_model,
                config=config,
            )
        )
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("Gemini Live session connected")

    async def send_audio(self, pcm_data: bytes) -> None:
        """Send a PCM audio chunk to Gemini Live."""
        if not self._session:
            return
        try:
            await self._session.send(
                input=genai_types.LiveClientRealtimeInput(
                    media_chunks=[
                        genai_types.Blob(
                            mime_type="audio/pcm;rate=16000",
                            data=pcm_data,
                        )
                    ]
                )
            )
        except Exception as e:
            logger.error("Failed to send audio to Gemini: %s", e)
            await self._on_error("GEMINI_TIMEOUT", False)

    async def send_text(self, text: str) -> None:
        """Send text input to Gemini (for demo mode or context replay)."""
        if not self._session:
            return
        try:
            await self._session.send(
                input=genai_types.LiveClientContent(
                    turns=[
                        genai_types.Content(
                            role="user",
                            parts=[genai_types.Part(text=text)],
                        )
                    ],
                    turn_complete=True,
                )
            )
        except Exception as e:
            logger.error("Failed to send text to Gemini: %s", e)
            await self._on_error("GEMINI_TIMEOUT", False)

    async def _receive_loop(self) -> None:
        """Background task: read text responses from Gemini Live."""
        try:
            async for message in self._session.receive():
                server_content = getattr(message, "server_content", None)
                if not server_content:
                    continue

                model_turn = getattr(server_content, "model_turn", None)
                if model_turn and model_turn.parts:
                    text_parts = [p.text for p in model_turn.parts if hasattr(p, "text") and p.text]
                    if text_parts:
                        combined = " ".join(text_parts)
                        is_complete = getattr(server_content, "turn_complete", False)
                        await self._on_text_response(combined, is_complete)

                if getattr(server_content, "turn_complete", False):
                    await self._on_status_change("listening")

        except asyncio.CancelledError:
            logger.info("Gemini receive loop cancelled")
        except Exception as e:
            logger.error("Gemini receive loop error: %s", e)
            await self._on_error("GEMINI_CONNECTION_LOST", True)

    async def close(self) -> None:
        """Close the Gemini Live connection."""
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task

        if self._exit_stack:
            with contextlib.suppress(Exception):
                await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None

        logger.info("Gemini Live session closed")
