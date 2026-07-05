# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the AssemblyAI batch transcription service.

Covers the VAD/WAV helpers, the submit phase (upload + transcript
creation over a faked ``httpx`` transport), the poll phase
(``check_job_status`` over a faked ``httpx.get``), and the merge/format
utilities that turn AssemblyAI results into the VTT-ish transcript text.

The submit phase normally runs through ``tracing_async_client`` (real
``httpx.AsyncClient`` with a correlation-header hook). Tests replace it
with a client wired to ``httpx.MockTransport`` — the same faking pattern
``test_outbound_trace_propagation.py`` uses for the same helper.
"""

from __future__ import annotations

import io
import json
import struct
import wave
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.services import assemblyai_transcription_service
from app.services.assemblyai_transcription_service import (
    AssemblyAiTranscriptionService,
    _ensure_wav,
    _extract_speech_regions,
    _format_timestamp,
    _merge_close_regions,
    _merge_segments,
    _words_to_utterances,
)
from app.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Callable

# --- helpers -----------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "database_url": "postgresql://t:t@l/t",
        "assemblyai_api_key": "test-api-key",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def _make_mono_wav(samples: list[int], framerate: int = 1000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def _make_stereo_wav(samples: list[int], framerate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


def _install_mock_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Replace tracing_async_client with a plain client on a MockTransport."""
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        assemblyai_transcription_service,
        "tracing_async_client",
        lambda **_kwargs: httpx.AsyncClient(transport=transport),
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# --- _ensure_wav ---------------------------------------------------------


class TestEnsureWav:
    def test_already_wav_is_returned_unchanged(self) -> None:
        existing_wav = _make_mono_wav([0, 100, -100], framerate=8000)
        assert _ensure_wav(existing_wav) == existing_wav

    def test_wraps_raw_pcm_with_wav_header(self) -> None:
        raw_pcm = struct.pack("<4h", 0, 0, 1000, 1000)  # 2 stereo frames
        wrapped = _ensure_wav(raw_pcm)

        assert wrapped[:4] == b"RIFF"
        with wave.open(io.BytesIO(wrapped), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 48000

    def test_downmix_averages_left_and_right_channels(self) -> None:
        # One stereo frame: left=100, right=300 -> mono sample should be 200.
        raw_pcm = struct.pack("<2h", 100, 300)
        wrapped = _ensure_wav(raw_pcm)

        with wave.open(io.BytesIO(wrapped), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        (sample,) = struct.unpack("<1h", frames)
        assert sample == 200

    def test_trims_trailing_partial_frame(self) -> None:
        # 2 full stereo frames (8 bytes) plus 2 stray bytes (partial frame).
        raw_pcm = struct.pack("<4h", 10, 20, 30, 40) + b"\x01\x02"
        wrapped = _ensure_wav(raw_pcm)

        with wave.open(io.BytesIO(wrapped), "rb") as wf:
            assert wf.getnframes() == 2


# --- _merge_close_regions --------------------------------------------------


class TestMergeCloseRegions:
    def test_merges_regions_within_gap(self) -> None:
        assert _merge_close_regions([(0, 100), (110, 200)], gap_samples=50) == [(0, 200)]

    def test_keeps_regions_beyond_gap_separate(self) -> None:
        assert _merge_close_regions([(0, 100), (300, 400)], gap_samples=50) == [
            (0, 100),
            (300, 400),
        ]

    def test_single_region_returned_unchanged(self) -> None:
        assert _merge_close_regions([(0, 50)], gap_samples=10) == [(0, 50)]


# --- _extract_speech_regions -----------------------------------------------


class TestExtractSpeechRegions:
    def test_malformed_bytes_return_single_passthrough_region(self) -> None:
        regions = _extract_speech_regions(b"not a wav file")

        assert len(regions) == 1
        assert regions[0].wav_data == b"not a wav file"
        assert regions[0].original_offset == 0.0

    def test_stereo_wav_returns_single_passthrough_region(self) -> None:
        stereo_wav = _make_stereo_wav([100, 100, 200, 200])

        regions = _extract_speech_regions(stereo_wav)

        assert len(regions) == 1
        assert regions[0].wav_data == stereo_wav
        assert regions[0].original_offset == 0.0

    def test_silence_only_audio_returns_single_passthrough_region(self) -> None:
        silent_wav = _make_mono_wav([0] * 200, framerate=1000)

        regions = _extract_speech_regions(silent_wav, threshold=500, min_silence_ms=500)

        assert len(regions) == 1
        assert regions[0].original_offset == 0.0

    def test_splits_two_speech_bursts_separated_by_long_silence(self) -> None:
        framerate = 1000
        burst = [2000] * 100  # well above threshold=500
        silence = [0] * 2000  # long enough to survive region-merge padding
        samples = burst + silence + burst
        wav_bytes = _make_mono_wav(samples, framerate=framerate)

        regions = _extract_speech_regions(wav_bytes, threshold=500, min_silence_ms=500)

        assert len(regions) == 2
        assert regions[0].original_offset == 0.0
        assert regions[1].original_offset == pytest.approx(1.95)
        for region in regions:
            with wave.open(io.BytesIO(region.wav_data), "rb") as wf:
                assert wf.getnchannels() == 1
                assert wf.getsampwidth() == 2
                assert wf.getframerate() == framerate


# --- submit phase (AssemblyAiTranscriptionService) --------------------------

_SILENCE_PCM = b"\x00\x00\x00\x00" * 50  # raw 48kHz/16-bit/stereo silence


def _channel_handler(
    calls: list[httpx.Request],
) -> Callable[[httpx.Request], httpx.Response]:
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        counter["n"] += 1
        if request.url.path.endswith("/upload"):
            return httpx.Response(
                200,
                json={"upload_url": f"https://cdn.example/upload-{counter['n']}"},
                request=request,
            )
        return httpx.Response(200, json={"id": f"transcript-{counter['n']}"}, request=request)

    return handler


class TestSubmitDualChannel:
    @pytest.mark.anyio
    async def test_submits_both_channels_with_expected_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(_settings())

        jobs = await service.submit_dual_channel(
            therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM
        )

        assert len(jobs) == 2
        assert {job["speaker"] for job in jobs} == {"Therapist", "Client"}
        for job in jobs:
            assert job["original_offset"] == 0.0
            assert job["transcript_id"].startswith("transcript-")

        upload_calls = [c for c in calls if c.url.path.endswith("/upload")]
        submit_calls = [c for c in calls if c.url.path.endswith("/transcript")]
        assert len(upload_calls) == 2
        assert len(submit_calls) == 2

    @pytest.mark.anyio
    async def test_upload_call_uses_hardcoded_300s_timeout_and_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(_settings())

        await service.submit_dual_channel(therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM)

        upload_call = next(c for c in calls if c.url.path.endswith("/upload"))
        assert upload_call.extensions["timeout"]["read"] == 300
        assert upload_call.headers["authorization"] == "test-api-key"
        assert upload_call.headers["content-type"] == "application/octet-stream"

    @pytest.mark.anyio
    async def test_submit_transcript_call_uses_hardcoded_30s_timeout_and_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(_settings())

        await service.submit_dual_channel(therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM)

        submit_call = next(c for c in calls if c.url.path.endswith("/transcript"))
        assert submit_call.extensions["timeout"]["read"] == 30
        assert submit_call.headers["authorization"] == "test-api-key"
        body: dict[str, Any] = json.loads(submit_call.content)
        assert body["language_code"] == "en"
        assert body["speech_model"] == "best"
        assert body["audio_url"].startswith("https://cdn.example/upload-")

    @pytest.mark.anyio
    async def test_submit_transcript_uses_configured_speech_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(_settings(assemblyai_speech_model="nano"))

        await service.submit_dual_channel(therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM)

        submit_call = next(c for c in calls if c.url.path.endswith("/transcript"))
        body: dict[str, Any] = json.loads(submit_call.content)
        assert body["speech_model"] == "nano"

    @pytest.mark.anyio
    async def test_upload_http_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"}, request=request)

        _install_mock_transport(monkeypatch, handler)
        service = AssemblyAiTranscriptionService(_settings())

        with pytest.raises(httpx.HTTPStatusError):
            await service.submit_dual_channel(
                therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM
            )

    @pytest.mark.anyio
    async def test_submit_transcript_http_error_propagates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/upload"):
                return httpx.Response(
                    200, json={"upload_url": "https://cdn.example/upload-1"}, request=request
                )
            return httpx.Response(422, json={"error": "unsupported audio"}, request=request)

        _install_mock_transport(monkeypatch, handler)
        service = AssemblyAiTranscriptionService(_settings())

        with pytest.raises(httpx.HTTPStatusError):
            await service.submit_dual_channel(
                therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM
            )

    @pytest.mark.anyio
    async def test_upload_timeout_propagates_uncaught(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("timed out", request=request)

        _install_mock_transport(monkeypatch, handler)
        service = AssemblyAiTranscriptionService(_settings())

        with pytest.raises(httpx.TimeoutException):
            await service.submit_dual_channel(
                therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM
            )


# --- poll phase (check_job_status) ------------------------------------------


class TestCheckJobStatus:
    def test_completed_status_returns_full_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
            captured["url"] = url
            captured["headers"] = headers
            captured["timeout"] = timeout
            payload = {"status": "completed", "text": "hi", "words": []}
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)

        status, data = AssemblyAiTranscriptionService.check_job_status("api-key-1", "transcript-1")

        assert status == "completed"
        assert data == {"status": "completed", "text": "hi", "words": []}
        assert captured["url"] == "https://api.assemblyai.com/v2/transcript/transcript-1"
        assert captured["headers"]["Authorization"] == "api-key-1"
        assert captured["timeout"] == 30

    def test_error_status_returns_error_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
            payload = {"status": "error", "error": "bad audio"}
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)

        status, data = AssemblyAiTranscriptionService.check_job_status("api-key-1", "transcript-2")

        assert status == "error"
        assert data == {"status": "error", "error": "bad audio"}

    def test_processing_status_returns_none_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
            return httpx.Response(200, json={"status": "queued"}, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)

        status, data = AssemblyAiTranscriptionService.check_job_status("api-key-1", "transcript-3")

        assert status == "processing"
        assert data is None

    def test_http_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
            return httpx.Response(404, request=httpx.Request("GET", url))

        monkeypatch.setattr(httpx, "get", fake_get)

        with pytest.raises(httpx.HTTPStatusError):
            AssemblyAiTranscriptionService.check_job_status("api-key-1", "missing-id")


# --- process_completed_jobs / merge utilities -------------------------------


class TestProcessCompletedJobs:
    def test_merges_words_into_utterances_with_speaker_and_offset(self) -> None:
        jobs_with_results = [
            (
                {"speaker": "Therapist", "original_offset": 0.0},
                {
                    "words": [
                        {"start": 0, "end": 500, "text": "Hello"},
                        {"start": 500, "end": 1000, "text": "there."},
                    ]
                },
            ),
            (
                {"speaker": "Client", "original_offset": 2.0},
                {"words": [{"start": 0, "end": 400, "text": "Hi."}]},
            ),
        ]

        transcript = AssemblyAiTranscriptionService.process_completed_jobs(jobs_with_results)

        assert transcript == ("[00:00:00]\nTherapist: Hello there.\n[00:00:02]\nClient: Hi.")

    def test_falls_back_to_text_when_no_words(self) -> None:
        jobs_with_results = [
            (
                {"speaker": "Therapist", "original_offset": 5.0},
                {"words": [], "text": "  Fallback text.  "},
            )
        ]

        transcript = AssemblyAiTranscriptionService.process_completed_jobs(jobs_with_results)

        assert transcript == "[00:00:05]\nTherapist: Fallback text."

    def test_skips_job_with_no_words_and_blank_text(self) -> None:
        jobs_with_results = [
            ({"speaker": "Therapist", "original_offset": 0.0}, {"words": [], "text": "   "})
        ]

        transcript = AssemblyAiTranscriptionService.process_completed_jobs(jobs_with_results)

        assert transcript == ""


class TestWordsToUtterances:
    def test_empty_segments_return_empty_list(self) -> None:
        assert _words_to_utterances([], "Therapist") == []

    def test_splits_on_gap_exceeding_threshold(self) -> None:
        segments = [
            {"start": 0.0, "end": 0.5, "text": "Hello"},
            {"start": 0.5, "end": 1.0, "text": "there."},
            {"start": 3.0, "end": 3.4, "text": "Later."},
        ]

        utterances = _words_to_utterances(segments, "Therapist", gap_threshold=1.5)

        assert utterances == [
            {"start": 0.0, "end": 1.0, "speaker": "Therapist", "text": "Hello there."},
            {"start": 3.0, "end": 3.4, "speaker": "Therapist", "text": "Later."},
        ]


class TestMergeSegments:
    def test_sorts_multiple_channels_by_start_time(self) -> None:
        channel_a = [{"start": 5.0, "speaker": "Client", "text": "Second."}]
        channel_b = [{"start": 1.0, "speaker": "Therapist", "text": "First."}]

        merged = _merge_segments(channel_a, channel_b)

        assert merged == "[00:00:01]\nTherapist: First.\n[00:00:05]\nClient: Second."


class TestFormatTimestamp:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "00:00:00"),
            (61.0, "00:01:01"),
            (3661.0, "01:01:01"),
            (59.9, "00:00:59"),  # truncates rather than rounding
        ],
    )
    def test_formats_hh_mm_ss(self, seconds: float, expected: str) -> None:
        assert _format_timestamp(seconds) == expected
