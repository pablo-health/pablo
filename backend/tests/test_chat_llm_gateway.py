# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for GeminiChatLLMGateway model-name normalization."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess

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
