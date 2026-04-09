# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Gemini text generation service for Practice Mode.

Uses regular Gemini streaming (not Live API) to generate patient responses
from transcribed therapist speech. Maintains conversation history.
"""

import logging
from collections.abc import Awaitable, Callable

from google import genai
from google.genai import types as genai_types

from ..settings import Settings

logger = logging.getLogger(__name__)


class GeminiTextSession:
    """Manages a Gemini conversation for practice mode.

    Receives transcribed text (from ASR), generates streaming patient responses.
    """

    def __init__(
        self,
        settings: Settings,
        system_prompt: str,
        on_text_response: Callable[[str, bool], Awaitable[None]],
        on_error: Callable[[str, bool], Awaitable[None]],
    ) -> None:
        self._settings = settings
        self._system_prompt = system_prompt
        self._on_text_response = on_text_response
        self._on_error = on_error
        self._client: genai.Client | None = None
        self._history: list[genai_types.Content] = []

    async def start(self) -> None:
        """Initialize the Gemini client."""
        self._client = genai.Client(
            vertexai=True,
            project=self._settings.gcp_project_id,
            location=self._settings.transcription_queue_location or "us-central1",
        )
        logger.info("Gemini text session started (model=%s)", self._settings.practice_gemini_model)

    async def generate_response(self, therapist_text: str) -> None:
        """Send therapist transcript to Gemini and stream the patient response."""
        if not self._client:
            return

        self._history.append(
            genai_types.Content(
                role="user",
                parts=[genai_types.Part(text=therapist_text)],
            )
        )

        try:
            response_text = ""
            stream = await self._client.aio.models.generate_content_stream(
                model=self._settings.practice_gemini_model,
                contents=self._history,  # type: ignore[arg-type]
                config=genai_types.GenerateContentConfig(
                    system_instruction=self._system_prompt,
                    temperature=0.8,
                    max_output_tokens=300,
                ),
            )
            async for chunk in stream:
                text = chunk.text
                if text:
                    response_text += text

            # Deliver the full response for TTS (TTS works better with complete sentences)
            if response_text.strip():
                self._history.append(
                    genai_types.Content(
                        role="model",
                        parts=[genai_types.Part(text=response_text)],
                    )
                )
                await self._on_text_response(response_text.strip(), True)

        except Exception as e:
            logger.error("Gemini generation error: %s", e)
            await self._on_error(f"AI generation error: {e}", False)

    async def send_text(self, text: str) -> None:
        """Send text input directly (for demo mode)."""
        await self.generate_response(text)

    async def close(self) -> None:
        """Clean up."""
        self._client = None
        self._history.clear()
        logger.info("Gemini text session closed")
