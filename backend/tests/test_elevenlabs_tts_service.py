# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for ElevenLabsTTSService — URL construction, request format, error handling.

The critical test here is that output_format is a URL query parameter, NOT a JSON
body field. ElevenLabs silently ignores unknown body fields and defaults to
mp3_44100_128, which caused a weeks-long bug where MP3 data was treated as raw PCM.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlparse

import pytest

from backend.app.services.elevenlabs_tts_service import (
    ELEVENLABS_API_BASE,
    ElevenLabsTTSError,
    ElevenLabsTTSService,
)
from backend.app.settings import Settings


@pytest.fixture
def settings():
    return Settings(
        elevenlabs_api_key="test-key",
        elevenlabs_voice_id="default-voice",
        elevenlabs_model_id="eleven_multilingual_v2",
        environment="development",
    )


@pytest.fixture
def tts(settings):
    return ElevenLabsTTSService(settings)


@pytest.fixture
def tts_custom_voice(settings):
    return ElevenLabsTTSService(settings, voice_id="custom-voice")


def _mock_client(captured: dict, *, status: int = 200, body: bytes = b"") -> MagicMock:
    """Create a mock httpx.AsyncClient that captures request details."""

    @asynccontextmanager
    async def mock_stream(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json", {})
        captured["headers"] = kwargs.get("headers", {})

        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.aread = AsyncMock(return_value=body)

        async def empty_iter(chunk_size=4800):
            return
            yield

        mock_resp.aiter_bytes = empty_iter
        yield mock_resp

    client = MagicMock()
    client.is_closed = False
    client.stream = mock_stream
    return client


class TestOutputFormatPlacement:
    """output_format MUST be a URL query parameter, never a JSON body field.

    ElevenLabs silently ignores unknown body fields and defaults to mp3_44100_128.
    This caused a weeks-long bug where MP3 data was treated as raw PCM, producing
    garbage audio that no streaming ASR could transcribe.
    """

    def test_output_format_in_url(self, tts):
        captured: dict[str, Any] = {}
        tts._client = _mock_client(captured)
        asyncio.run(tts.synthesize_stream("Hello", AsyncMock()))

        parsed = urlparse(captured["url"])
        qs = parse_qs(parsed.query)
        assert "output_format" in qs, "output_format missing from URL query params"
        assert qs["output_format"] == ["pcm_24000"]

    def test_output_format_not_in_body(self, tts):
        captured: dict[str, Any] = {}
        tts._client = _mock_client(captured)
        asyncio.run(tts.synthesize_stream("Hello", AsyncMock()))

        assert "output_format" not in captured["json"], (
            "output_format must NOT be in JSON body — ElevenLabs ignores it and defaults to MP3"
        )


class TestURLConstruction:
    def test_url_contains_voice_id(self, tts_custom_voice):
        captured: dict[str, Any] = {}
        tts_custom_voice._client = _mock_client(captured)
        asyncio.run(tts_custom_voice.synthesize_stream("Test", AsyncMock()))

        assert "/custom-voice/stream" in captured["url"]

    def test_url_uses_streaming_endpoint(self, tts):
        captured: dict[str, Any] = {}
        tts._client = _mock_client(captured)
        asyncio.run(tts.synthesize_stream("Test", AsyncMock()))

        assert captured["url"].startswith(ELEVENLABS_API_BASE)
        assert "/stream?" in captured["url"]


class TestRequestBody:
    def test_body_contains_text_and_model(self, tts):
        captured: dict[str, Any] = {}
        tts._client = _mock_client(captured)
        asyncio.run(tts.synthesize_stream("Hello world", AsyncMock()))

        body = captured["json"]
        assert body["text"] == "Hello world"
        assert body["model_id"] == "eleven_multilingual_v2"

    def test_body_contains_voice_settings(self, tts):
        captured: dict[str, Any] = {}
        tts._client = _mock_client(captured)
        asyncio.run(tts.synthesize_stream("Test", AsyncMock()))

        vs = captured["json"]["voice_settings"]
        assert vs["stability"] == 0.5
        assert vs["similarity_boost"] == 0.75

    def test_api_key_in_headers(self, tts):
        captured: dict[str, Any] = {}
        tts._client = _mock_client(captured)
        asyncio.run(tts.synthesize_stream("Test", AsyncMock()))

        assert captured["headers"]["xi-api-key"] == "test-key"


class TestEmptyInput:
    def test_empty_text_skips_api_call(self, tts):
        callback = AsyncMock()
        asyncio.run(tts.synthesize_stream("", callback))
        callback.assert_not_called()

    def test_whitespace_only_skips_api_call(self, tts):
        callback = AsyncMock()
        asyncio.run(tts.synthesize_stream("   ", callback))
        callback.assert_not_called()


class TestErrorHandling:
    def test_non_200_raises_tts_error(self, tts):
        captured: dict[str, Any] = {}
        tts._client = _mock_client(captured, status=401, body=b'{"detail":"invalid_api_key"}')

        with pytest.raises(ElevenLabsTTSError, match="401"):
            asyncio.run(tts.synthesize_stream("Hello", AsyncMock()))
