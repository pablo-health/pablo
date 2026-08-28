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
    _format_timestamp,
    _is_adts_aac,
    _merge_close_regions,
    _merge_segments,
    _prepare_speech_audio,
    _prepare_whole_audio,
    _words_to_utterances,
    sniff_audio_container,
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

    def test_mono_pcm_is_preserved_sample_for_sample(self) -> None:
        # The therapist sidecar is mono. Wrapping it as stereo halves the frame
        # count and averages adjacent (unrelated) samples into one, which mangles
        # the waveform into something that transcribes to nothing -- and the SOAP
        # then comes back as unanchored "not described in the transcript"
        # placeholders. Every sample must survive untouched.
        raw_pcm = struct.pack("<4h", 100, 300, -200, 400)
        wrapped = _ensure_wav(raw_pcm, n_channels=1)

        with wave.open(io.BytesIO(wrapped), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getnframes() == 4
            frames = wf.readframes(wf.getnframes())
        assert struct.unpack("<4h", frames) == (100, 300, -200, 400)

    def test_mono_pcm_wrapped_as_stereo_is_the_corruption_this_guards(self) -> None:
        # Pins the old behavior as wrong rather than merely different: the same
        # mono bytes read as stereo lose half their frames and average pairs that
        # were never a stereo image of anything.
        raw_pcm = struct.pack("<4h", 100, 300, -200, 400)

        as_stereo = _ensure_wav(raw_pcm, n_channels=2)
        with wave.open(io.BytesIO(as_stereo), "rb") as wf:
            assert wf.getnframes() == 2  # half the real frames
            frames = wf.readframes(wf.getnframes())
        assert struct.unpack("<2h", frames) == (200, 100)  # neighbours averaged

    def test_multichannel_downmix_averages_every_channel(self) -> None:
        # One 4-channel frame: the fold must span all channels, not just the first pair.
        raw_pcm = struct.pack("<4h", 100, 200, 300, 400)
        wrapped = _ensure_wav(raw_pcm, n_channels=4)

        with wave.open(io.BytesIO(wrapped), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
        assert struct.unpack("<1h", frames) == (250,)

    def test_riff_payload_ignores_the_declared_channel_count(self) -> None:
        # A self-describing payload must never be re-interpreted: current clients
        # send WAV, and the declared count is only a fallback for headerless PCM.
        existing_wav = _make_mono_wav([0, 100, -100], framerate=8000)
        assert _ensure_wav(existing_wav, n_channels=2) == existing_wav

    def test_rejects_nonsense_channel_count(self) -> None:
        with pytest.raises(ValueError, match="n_channels must be >= 1"):
            _ensure_wav(struct.pack("<2h", 1, 2), n_channels=0)

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


# --- _prepare_speech_audio ---------------------------------------------------


class TestPrepareSpeechAudio:
    def test_corrupt_riff_passes_through_with_identity_map(self) -> None:
        corrupt = b"RIFF" + b"\x00" * 10  # RIFF magic but not a parseable WAV

        speech_wav, offset_map = _prepare_speech_audio(corrupt)

        assert speech_wav == corrupt
        assert offset_map == [[0.0, 0.0]]

    def test_stereo_wav_passes_through_with_identity_map(self) -> None:
        stereo_wav = _make_stereo_wav([100, 100, 200, 200])

        speech_wav, offset_map = _prepare_speech_audio(stereo_wav)

        assert speech_wav == stereo_wav
        assert offset_map == [[0.0, 0.0]]

    def test_silence_only_audio_passes_through_whole(self) -> None:
        silent_wav = _make_mono_wav([0] * 200, framerate=1000)

        speech_wav, offset_map = _prepare_speech_audio(
            silent_wav, threshold=500, min_silence_ms=500
        )

        assert speech_wav == silent_wav
        assert offset_map == [[0.0, 0.0]]

    def test_concatenates_bursts_with_gap_and_offset_map(self) -> None:
        framerate = 1000
        burst = [2000] * 100  # well above threshold=500
        silence = [0] * 2000  # long enough to survive region-merge padding
        samples = burst + silence + burst
        wav_bytes = _make_mono_wav(samples, framerate=framerate)

        speech_wav, offset_map = _prepare_speech_audio(wav_bytes, threshold=500, min_silence_ms=500)

        # Region 1: samples 0-750 (burst + silence window + padding).
        # Region 2: samples 1950-2200 (padding before the second burst,
        # clamped to the end of the signal). Concatenated with a 0.5s gap:
        # region 2 starts at 0.75 + 0.5 = 1.25s in the speech-only file and
        # at 1.95s in the original recording.
        assert offset_map == [[0.0, 0.0], [1.25, 1.95]]
        with wave.open(io.BytesIO(speech_wav), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == framerate
            # 0.75s region + 0.5s gap + 0.25s region
            assert wf.getnframes() == 1500


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


# --- whole-file submission (AAC / no-VAD) ------------------------------

# Minimal fake ADTS AAC: 0xFF 0xF1 sync + layer/no-CRC, then arbitrary payload.
_FAKE_AAC = b"\xff\xf1" + bytes(64)


class TestSniffAudioContainer:
    def test_riff_is_wav(self) -> None:
        assert sniff_audio_container(_make_mono_wav([0, 0, 0])) == ("wav", "audio/wav")

    def test_adts_is_aac(self) -> None:
        assert sniff_audio_container(_FAKE_AAC) == ("aac", "audio/aac")

    def test_unknown_defaults_to_wav(self) -> None:
        assert sniff_audio_container(b"\x01\x02\x03\x04") == ("wav", "audio/wav")

    def test_adts_sync_detection(self) -> None:
        assert _is_adts_aac(_FAKE_AAC)
        assert not _is_adts_aac(_make_mono_wav([0, 0]))
        assert not _is_adts_aac(b"\xff\x0f")  # sync high byte only, wrong low nibble


class TestPrepareWholeAudio:
    def test_aac_passes_through_untouched_with_identity_map(self) -> None:
        prepared, offset_map = _prepare_whole_audio(_FAKE_AAC)
        assert prepared == _FAKE_AAC
        assert offset_map == [[0.0, 0.0]]

    def test_wav_passes_through_untouched(self) -> None:
        wav = _make_mono_wav([1, 2, 3])
        prepared, offset_map = _prepare_whole_audio(wav)
        assert prepared == wav
        assert offset_map == [[0.0, 0.0]]

    def test_raw_pcm_is_wrapped_to_wav(self) -> None:
        raw = struct.pack("<4h", 1, 2, 3, 4)
        prepared, offset_map = _prepare_whole_audio(raw, n_channels=1)
        assert prepared[:4] == b"RIFF"
        assert offset_map == [[0.0, 0.0]]


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
            # Silence-only audio passes through whole, so the map is identity.
            assert job["offset_map"] == [[0.0, 0.0]]
            assert job["transcript_id"].startswith("transcript-")

        upload_calls = [c for c in calls if c.url.path.endswith("/upload")]
        submit_calls = [c for c in calls if c.url.path.endswith("/transcript")]
        assert len(upload_calls) == 2
        assert len(submit_calls) == 2

    @pytest.mark.anyio
    async def test_aac_is_submitted_whole_even_with_vad_enabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # VAD can't run on compressed audio (no decode step), so an AAC payload
        # is uploaded byte-for-byte with an identity offset map regardless of
        # the flag.
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(_settings(assemblyai_vad_enabled=True))

        jobs = await service.submit_dual_channel(therapist_audio=_FAKE_AAC, client_audio=_FAKE_AAC)

        assert all(job["offset_map"] == [[0.0, 0.0]] for job in jobs)
        upload_calls = [c for c in calls if c.url.path.endswith("/upload")]
        assert len(upload_calls) == 2
        assert all(c.content == _FAKE_AAC for c in upload_calls)

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
    async def test_default_submit_body_has_no_speaker_labels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(_settings())

        jobs = await service.submit_dual_channel(
            therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM
        )

        submit_calls = [c for c in calls if c.url.path.endswith("/transcript")]
        assert len(submit_calls) == 2
        for c in submit_calls:
            body: dict[str, Any] = json.loads(c.content)
            assert "speaker_labels" not in body
        assert [job["diarized"] for job in jobs] == [False, False]

    @pytest.mark.anyio
    async def test_speaker_labels_sent_only_for_configured_channels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(
            _settings(assemblyai_speaker_labels_channels=["Client"])
        )

        jobs = await service.submit_dual_channel(
            therapist_audio=_SILENCE_PCM, client_audio=_SILENCE_PCM
        )

        submit_calls = [c for c in calls if c.url.path.endswith("/transcript")]
        assert len(submit_calls) == 2
        therapist_body: dict[str, Any] = json.loads(submit_calls[0].content)
        client_body: dict[str, Any] = json.loads(submit_calls[1].content)
        assert "speaker_labels" not in therapist_body
        assert client_body["speaker_labels"] is True
        assert [job["diarized"] for job in jobs] == [False, True]

    @pytest.mark.anyio
    async def test_audio_url_factory_bypasses_provider_upload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[httpx.Request] = []
        _install_mock_transport(monkeypatch, _channel_handler(calls))
        service = AssemblyAiTranscriptionService(_settings())
        staged: list[tuple[str, bytes]] = []

        def factory(speaker: str, wav_bytes: bytes) -> str:
            staged.append((speaker, wav_bytes))
            return f"https://storage.example/{speaker.lower()}"

        jobs = await service.submit_dual_channel(
            therapist_audio=_SILENCE_PCM,
            client_audio=_SILENCE_PCM,
            audio_url_factory=factory,
        )

        # The factory received the prepared (WAV-wrapped) audio per channel...
        assert [speaker for speaker, _ in staged] == ["Therapist", "Client"]
        for _, wav_bytes in staged:
            assert wav_bytes[:4] == b"RIFF"
        # ...and its URLs were submitted instead of AssemblyAI /upload ones.
        assert all(not c.url.path.endswith("/upload") for c in calls)
        submitted_urls = {
            json.loads(c.content)["audio_url"] for c in calls if c.url.path.endswith("/transcript")
        }
        assert submitted_urls == {
            "https://storage.example/therapist",
            "https://storage.example/client",
        }
        assert len(jobs) == 2

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


class TestParseResult:
    def test_offset_map_remaps_words_back_to_original_timeline(self) -> None:
        job_meta = {
            "transcript_id": "t1",
            "speaker": "Therapist",
            # Region 1 sat at 10s in the original recording; region 2 starts
            # at 5s in the concatenated file and sat at 100s originally.
            "offset_map": [[0.0, 10.0], [5.0, 100.0]],
        }
        result = {
            "words": [
                {"start": 1000, "end": 1500, "text": "Early."},
                {"start": 6000, "end": 6500, "text": "Late."},
            ]
        }

        utterances = AssemblyAiTranscriptionService.parse_result(job_meta, result)

        assert utterances == [
            {"start": 11.0, "end": 11.5, "speaker": "Therapist", "text": "Early."},
            {"start": 101.0, "end": 101.5, "speaker": "Therapist", "text": "Late."},
        ]

    def test_legacy_original_offset_still_applies(self) -> None:
        job_meta = {"speaker": "Client", "original_offset": 2.0}
        result = {"words": [{"start": 0, "end": 400, "text": "Hi."}]}

        utterances = AssemblyAiTranscriptionService.parse_result(job_meta, result)

        assert utterances == [{"start": 2.0, "end": 2.4, "speaker": "Client", "text": "Hi."}]

    def test_falls_back_to_text_when_no_words(self) -> None:
        job_meta = {"speaker": "Therapist", "original_offset": 5.0}
        result = {"words": [], "text": "  Fallback text.  "}

        utterances = AssemblyAiTranscriptionService.parse_result(job_meta, result)

        assert utterances == [
            {"start": 5.0, "end": 5.0, "speaker": "Therapist", "text": "Fallback text."}
        ]

    def test_no_words_and_blank_text_yield_nothing(self) -> None:
        job_meta = {"speaker": "Therapist", "original_offset": 0.0}
        result = {"words": [], "text": "   "}

        assert AssemblyAiTranscriptionService.parse_result(job_meta, result) == []

    def test_diarized_words_keep_channel_and_letter(self) -> None:
        job_meta = {
            "speaker": "Client",
            "diarized": True,
            "offset_map": [[0.0, 10.0]],
        }
        result = {
            "words": [
                {"start": 0, "end": 500, "text": "Hello.", "speaker": "A"},
                {"start": 500, "end": 1000, "text": "there.", "speaker": "A"},
                {"start": 1000, "end": 1500, "text": "Hi.", "speaker": "B"},
            ]
        }

        utterances = AssemblyAiTranscriptionService.parse_result(job_meta, result)

        assert utterances == [
            {"start": 10.0, "end": 11.0, "speaker": "Client A", "text": "Hello. there."},
            {"start": 11.0, "end": 11.5, "speaker": "Client B", "text": "Hi."},
        ]

    def test_undiarized_job_ignores_word_speaker_field(self) -> None:
        job_meta = {
            "speaker": "Client",
            "diarized": False,
            "offset_map": [[0.0, 10.0]],
        }
        result = {
            "words": [
                {"start": 0, "end": 500, "text": "Hello.", "speaker": "A"},
                {"start": 500, "end": 1000, "text": "there.", "speaker": "A"},
                {"start": 1000, "end": 1500, "text": "Hi.", "speaker": "B"},
            ]
        }

        utterances = AssemblyAiTranscriptionService.parse_result(job_meta, result)

        assert utterances == [
            {"start": 10.0, "end": 11.5, "speaker": "Client", "text": "Hello. there. Hi."}
        ]


class TestMergeUtterances:
    def test_merges_channels_sorted_by_start_time(self) -> None:
        jobs = [
            {
                "speaker": "Client",
                "utterances": [{"start": 2.0, "end": 2.4, "speaker": "Client", "text": "Hi."}],
            },
            {
                "speaker": "Therapist",
                "utterances": [
                    {"start": 0.0, "end": 1.0, "speaker": "Therapist", "text": "Hello there."}
                ],
            },
        ]

        transcript = AssemblyAiTranscriptionService.merge_utterances(jobs)

        assert transcript == "[00:00:00]\nTherapist: Hello there.\n[00:00:02]\nClient: Hi."

    def test_jobs_without_utterances_contribute_nothing(self) -> None:
        assert AssemblyAiTranscriptionService.merge_utterances([{"speaker": "Therapist"}]) == ""


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
