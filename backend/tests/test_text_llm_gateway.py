# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The text gateway: one usage record per call, optional prompt cache that
can only fall back, never fail the call."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.reliability import SINGLE_ATTEMPT
from app.services import text_llm_gateway
from app.services.text_llm_gateway import generate_text


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    text_llm_gateway._caches.clear()


def _client(*, cache_error: Exception | None = None) -> Any:
    usage = SimpleNamespace(
        prompt_token_count=2100,
        candidates_token_count=40,
        total_token_count=2290,
        cached_content_token_count=1900,
        thoughts_token_count=150,
    )
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        text="  ANSWER-TEXT  ", usage_metadata=usage
    )
    if cache_error is not None:
        client.caches.create.side_effect = cache_error
    else:
        client.caches.create.return_value = SimpleNamespace(name="cachedContents/abc")
    return client


def _call(client: Any, **overrides: Any) -> str:
    kwargs: dict[str, Any] = {
        "model": "google:gemini-3.5-flash",
        "system_prompt": "SYSTEM-MARKER",
        "user_content": "USER-MARKER",
        "temperature": 0.0,
        "max_output_tokens": 256,
        "policy": SINGLE_ATTEMPT,
        "client": client,
    }
    kwargs.update(overrides)
    return generate_text(**kwargs)


def test_call_writes_one_content_free_record_with_cache_and_thoughts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    out = _call(_client(), feature="inbox_triage")

    assert out == "ANSWER-TEXT"
    (record,) = [r for r in caplog.records if r.getMessage() == "llm_call"]
    assert record.llm_model == "gemini-3.5-flash"  # provider prefix stripped
    assert record.llm_feature == "inbox_triage"
    assert record.llm_prompt_tokens == 2100
    assert record.llm_cached_tokens == 1900
    assert record.llm_thoughts_tokens == 150
    blob = repr(vars(record))
    assert "MARKER" not in blob
    assert "ANSWER" not in blob


def test_prompt_is_sent_inline_by_default() -> None:
    client = _client()
    _call(client)
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.system_instruction == "SYSTEM-MARKER"
    assert config.cached_content is None
    client.caches.create.assert_not_called()


def test_cache_is_used_when_asked_and_reused_for_the_same_prompt() -> None:
    client = _client()
    _call(client, cache_system_prompt=True)
    _call(client, cache_system_prompt=True)

    assert client.caches.create.call_count == 1
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.cached_content == "cachedContents/abc"
    assert config.system_instruction is None  # the cache holds it


def test_a_different_prompt_gets_its_own_cache() -> None:
    client = _client()
    _call(client, cache_system_prompt=True)
    _call(client, cache_system_prompt=True, system_prompt="OTHER")
    assert client.caches.create.call_count == 2


def test_cache_failure_falls_back_to_inline_and_is_not_retried_every_call() -> None:
    client = _client(cache_error=RuntimeError("prompt too short"))
    assert _call(client, cache_system_prompt=True) == "ANSWER-TEXT"
    assert _call(client, cache_system_prompt=True) == "ANSWER-TEXT"

    assert client.caches.create.call_count == 1  # backed off after the failure
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.system_instruction == "SYSTEM-MARKER"
    assert config.cached_content is None


def test_json_config_is_forwarded_only_when_set() -> None:
    client = _client()
    _call(client, response_mime_type="application/json", response_schema={"type": "object"})
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"

    plain = _client()
    _call(plain)
    assert plain.models.generate_content.call_args.kwargs["config"].response_mime_type is None
