"""Unit tests for the Vertex client factories."""

from __future__ import annotations

import builtins

import pytest

from backend.app.reliability import LLM_REQUEST
from backend.app.services.vertex_client import (
    DEFAULT_VERTEX_TIMEOUT_SECONDS,
    anthropic_vertex_client,
    async_anthropic_vertex_client,
    vertex_genai_client,
)


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
    calls), and the SDK's own backoff could run one engine attempt past
    ``LLM_REQUEST``'s attempt bound and into its deadline.
    """
    client = anthropic_vertex_client()
    assert client.max_retries == 0


def test_anthropic_vertex_client_uses_the_claude_project_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment can serve Claude from a project other than the ambient one."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "claude-project")
    client = anthropic_vertex_client()
    assert client.project_id == "claude-project"


def test_anthropic_vertex_client_falls_back_to_the_ambient_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no Claude-specific project, behaviour is unchanged."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
    client = anthropic_vertex_client()
    assert client.project_id == "ambient-project"


def test_anthropic_vertex_client_treats_an_empty_claude_project_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty variable (a blank env entry) must not select an empty project."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "")
    client = anthropic_vertex_client()
    assert client.project_id == "ambient-project"


def test_async_client_uses_the_claude_project_and_the_same_retry_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The streaming client is the sync one's sibling: same project, same retry policy."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "claude-project")

    client = async_anthropic_vertex_client()

    assert client.project_id == "claude-project"
    assert client.max_retries == 0


def test_async_client_falls_back_to_the_ambient_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)

    assert async_anthropic_vertex_client().project_id == "ambient-project"


def test_default_client_bound_fits_the_request_preset_twice() -> None:
    """Every request-path caller on the default client (chat, agent, embedding,
    navigation) can retry an attempt that failed at its bound."""
    assert LLM_REQUEST.attempt_timeout is not None
    assert LLM_REQUEST.attempt_timeout >= DEFAULT_VERTEX_TIMEOUT_SECONDS
