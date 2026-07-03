"""Unit tests for the Vertex client factories."""

from __future__ import annotations

import builtins

import pytest

from backend.app.services.vertex_client import anthropic_vertex_client, vertex_genai_client


def test_runtime_error_when_genai_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def boom(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("google"):
            raise ImportError("simulated missing google-genai")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", boom)
    with pytest.raises(RuntimeError, match="google-genai"):
        vertex_genai_client()


def test_anthropic_vertex_client_disables_sdk_internal_retries() -> None:
    """The SDK's own retry loop must stay off — the reliability engine owns retries.

    Without ``max_retries=0``, a persistent 429 would stack 2 engine
    attempts against up to 3 SDK-internal tries each (6 total HTTP
    calls), and the SDK's own backoff could run past ``LLM_REQUEST``'s
    25s deadline from inside a single engine attempt.
    """
    client = anthropic_vertex_client()
    assert client.max_retries == 0
