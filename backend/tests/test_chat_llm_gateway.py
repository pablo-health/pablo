# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for GeminiChatLLMGateway model-name normalization."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
from app.services.chat_llm_gateway import GeminiChatLLMGateway


def test_normalize_strips_google_prefix() -> None:
    gateway = GeminiChatLLMGateway()
    assert gateway._normalize_model("google:gemini-2.5-flash") == "gemini-2.5-flash"


def test_normalize_passthrough_when_no_prefix() -> None:
    gateway = GeminiChatLLMGateway()
    assert gateway._normalize_model("gemini-2.5-flash") == "gemini-2.5-flash"


def test_normalize_warns_once_per_process(caplog) -> None:  # type: ignore[no-untyped-def]
    gateway = GeminiChatLLMGateway()
    with caplog.at_level(logging.WARNING, logger="app.services.chat_llm_gateway"):
        gateway._normalize_model("google:gemini-2.5-flash")
        gateway._normalize_model("google:gemini-2.5-pro")
    warnings = [r for r in caplog.records if "stripping" in r.getMessage()]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# Stream-establishment retry (hermetic — fakes ``client.aio.models``)
# ---------------------------------------------------------------------------


@dataclass
class _FakeCandidate:
    finish_reason: str | None = None


@dataclass
class _FakeChunk:
    text: str = ""
    candidates: list[Any] = field(default_factory=list)
    usage_metadata: Any = None


class _FakeModels:
    """Scripts one outcome per call to ``generate_content_stream``.

    Each scripted outcome is either an exception (raised as if the
    connect attempt failed) or a list of chunks (yielded as the
    stream). Mirrors the shape the real ``google.genai`` async client
    exposes closely enough for ``_open_stream`` to exercise unmodified.
    """

    def __init__(self, outcomes: list[BaseException | list[_FakeChunk]]) -> None:
        self._outcomes = list(outcomes)
        self.call_count = 0

    async def generate_content_stream(self, *, model: str, contents: Any, config: Any) -> Any:
        del model, contents, config
        self.call_count += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        chunks = outcome

        async def _gen() -> Any:
            for chunk in chunks:
                yield chunk

        return _gen()


class _FakeAio:
    def __init__(self, models: _FakeModels) -> None:
        self.models = models


class _FakeClient:
    def __init__(self, outcomes: list[BaseException | list[_FakeChunk]]) -> None:
        self.aio = _FakeAio(_FakeModels(outcomes))


def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.chat_llm_gateway._retry_sleep", _sleep)


class TestStreamEstablishmentRetry:
    def test_connect_failure_then_success_is_retried_transparently(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_real_sleep(monkeypatch)
        gateway = GeminiChatLLMGateway()
        fake_client = _FakeClient(
            [
                httpx.ConnectError("refused"),
                [_FakeChunk(text="hello", candidates=[_FakeCandidate(finish_reason="STOP")])],
            ]
        )
        gateway._client = fake_client

        async def _go() -> tuple[list[str], list[str]]:
            deltas: list[str] = []
            finishes: list[str] = []
            async for ev in gateway.stream_completion(
                model="gemini-2.5-flash",
                system_prompt="sys",
                prior_turns=[],
                new_user_text="hi",
                max_output_tokens=64,
            ):
                if ev.delta:
                    deltas.append(ev.delta)
                if ev.finish_reason:
                    finishes.append(ev.finish_reason)
            return deltas, finishes

        deltas, finishes = asyncio.run(_go())
        assert deltas == ["hello"]
        assert finishes == ["stop"]
        assert fake_client.aio.models.call_count == 2

    def test_non_retryable_open_failure_surfaces_as_error_event(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _no_real_sleep(monkeypatch)
        gateway = GeminiChatLLMGateway()
        fake_client = _FakeClient([ValueError("not a transient failure")])
        gateway._client = fake_client

        async def _go() -> list[Any]:
            events = []
            async for ev in gateway.stream_completion(
                model="gemini-2.5-flash",
                system_prompt="sys",
                prior_turns=[],
                new_user_text="hi",
                max_output_tokens=64,
            ):
                events.append(ev)
            return events

        events = asyncio.run(_go())
        assert len(events) == 1
        assert events[0].finish_reason == "error"
        assert fake_client.aio.models.call_count == 1

    def test_mid_stream_failure_after_first_chunk_is_not_retried_here(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The gateway only retries pre-first-chunk connect failures.

        A failure raised while iterating *after* the first chunk was
        already yielded live to the caller is a different axis
        (``ChatTurnService`` owns the whole-generation retry for that
        case) — this gateway must not retry it.
        """
        _no_real_sleep(monkeypatch)
        gateway = GeminiChatLLMGateway()

        class _FailingModels(_FakeModels):
            async def generate_content_stream(
                self, *, model: str, contents: Any, config: Any
            ) -> Any:
                del model, contents, config
                self.call_count += 1

                async def _gen() -> Any:
                    yield _FakeChunk(text="partial")
                    raise httpx.ReadTimeout("dropped mid-stream")

                return _gen()

        fake_client = _FakeClient([])
        fake_client.aio.models = _FailingModels([])
        gateway._client = fake_client

        async def _go() -> list[Any]:
            events = []
            async for ev in gateway.stream_completion(
                model="gemini-2.5-flash",
                system_prompt="sys",
                prior_turns=[],
                new_user_text="hi",
                max_output_tokens=64,
            ):
                events.append(ev)
            return events

        events = asyncio.run(_go())
        assert events[0].delta == "partial"
        assert events[-1].finish_reason == "error"
        assert fake_client.aio.models.call_count == 1


# ---------------------------------------------------------------------------
# Vertex AI end-to-end (real network, skipped unless ADC is available)
# ---------------------------------------------------------------------------


def _adc_available() -> bool:
    """True iff Application Default Credentials are reachable.

    Detected via ``gcloud auth application-default print-access-token`` —
    works whether the runner has user ADC, a service account JSON via
    ``GOOGLE_APPLICATION_CREDENTIALS``, or the GCE/GKE metadata server.
    """
    gcloud_path = shutil.which("gcloud")
    if not gcloud_path:
        return False
    try:
        result = subprocess.run(  # noqa: S603 — fixed argv from shutil.which, no shell
            [gcloud_path, "auth", "application-default", "print-access-token"],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


_ADC_REASON = (
    "Vertex AI ADC not available — run `gcloud auth application-default "
    "login` and set GOOGLE_CLOUD_PROJECT to enable this test"
)
_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
_E2E_SKIP = pytest.mark.skipif(
    not (_PROJECT and _adc_available()),
    reason=_ADC_REASON,
)


def _set_vertex_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the runtime env Vertex AI needs.

    ``GOOGLE_CLOUD_LOCATION`` defaults to ``global``: newer Gemini
    models (e.g. ``gemini-3.5-flash``) are only published as multi-
    region endpoints and return 404 in single-region projects like
    ``us-central1``. ``global`` is a strict superset, so tests stay
    portable.
    """
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv(
        "GOOGLE_CLOUD_LOCATION",
        os.environ.get("GOOGLE_CLOUD_LOCATION") or "global",
    )


@_E2E_SKIP
def test_real_vertex_stream_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: real Vertex call, gateway streams non-empty text."""
    _set_vertex_env(monkeypatch)

    async def _go() -> tuple[list[str], list[str]]:
        gateway = GeminiChatLLMGateway()
        deltas: list[str] = []
        finishes: list[str] = []
        async for ev in gateway.stream_completion(
            model="gemini-2.5-flash",
            system_prompt="You are a concise assistant.",
            prior_turns=[],
            new_user_text="In one short sentence, what is hypertension?",
            max_output_tokens=128,
        ):
            if ev.delta:
                deltas.append(ev.delta)
            if ev.finish_reason:
                finishes.append(ev.finish_reason)
        return deltas, finishes

    deltas, finishes = asyncio.run(_go())
    assert deltas, "expected at least one delta event from Vertex"
    assert sum(len(d) for d in deltas) > 0, "expected non-empty assistant text"
    assert finishes in (["stop"], ["length"]), (
        f"expected single stop/length finish, got {finishes!r}"
    )


@_E2E_SKIP
def test_real_vertex_strips_google_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: ``google:`` prefix must not reach Vertex.

    Without the prefix-strip in ``GeminiChatLLMGateway._normalize_model``,
    Vertex returns ``400 INVALID_ARGUMENT`` ("Invalid Endpoint name…
    publishers/google/models/google:gemini-2.5-flash"). The gateway
    catches that and emits ``finish_reason='error'`` with an empty
    assistant row — the THERAPY-1cqc symptom this PR fixes.
    """
    _set_vertex_env(monkeypatch)

    async def _go() -> str | None:
        gateway = GeminiChatLLMGateway()
        last_finish: str | None = None
        chars = 0
        async for ev in gateway.stream_completion(
            model="google:gemini-2.5-flash",
            system_prompt="You are a concise assistant.",
            prior_turns=[],
            new_user_text="Reply with exactly the word OK.",
            max_output_tokens=32,
        ):
            if ev.delta:
                chars += len(ev.delta)
            if ev.finish_reason:
                last_finish = ev.finish_reason
        assert chars > 0, "expected non-empty stream after prefix-strip"
        return last_finish

    assert asyncio.run(_go()) in {"stop", "length"}
