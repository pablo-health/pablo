# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Base ASR provider interface for Practice Mode real-time transcription."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ...settings import Settings

logger = logging.getLogger(__name__)


class AsrProvider(ABC):
    """Abstract base for streaming speech-to-text providers.

    Receives PCM audio chunks, fires callbacks when speech is recognized.
    """

    def __init__(
        self,
        on_transcript: Callable[[str, bool], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> None:
        self._on_transcript = on_transcript
        self._on_error = on_error

    @abstractmethod
    async def start(self) -> None:
        """Open the streaming connection."""

    @abstractmethod
    async def send_audio(self, pcm_data: bytes) -> None:
        """Send a PCM audio chunk (16kHz, 16-bit, mono)."""

    @abstractmethod
    async def stop(self) -> None:
        """Close the streaming connection and release resources."""


class AsrProviderFactory:
    """Create ASR providers based on configuration."""

    @staticmethod
    def create(
        settings: Settings,
        on_transcript: Callable[[str, bool], Awaitable[None]],
        on_error: Callable[[str], Awaitable[None]],
    ) -> AsrProvider:
        provider = settings.asr_provider.lower()

        if provider == "assemblyai":
            from .assemblyai_provider import AssemblyAiAsrProvider

            return AssemblyAiAsrProvider(
                api_key=settings.assemblyai_api_key.get_secret_value(),
                on_transcript=on_transcript,
                on_error=on_error,
            )

        if provider == "google":
            from .google_stt_provider import GoogleSttAsrProvider

            return GoogleSttAsrProvider(
                settings=settings,
                on_transcript=on_transcript,
                on_error=on_error,
            )

        raise ValueError(f"Unknown ASR provider: {provider}")
