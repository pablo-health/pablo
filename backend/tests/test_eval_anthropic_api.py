# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

from __future__ import annotations

from unittest.mock import patch

import pytest
from evals.anthropic_api import direct_anthropic_gateway, direct_anthropic_model


def test_direct_anthropic_model_removes_only_the_eval_prefix() -> None:
    assert direct_anthropic_model("anthropic-api:claude-sonnet-4-6") == "claude-sonnet-4-6"
    auto_model = direct_anthropic_model("anthropic-api-auto:claude-sonnet-4-6")
    assert auto_model == "auto:claude-sonnet-4-6"
    assert direct_anthropic_model("claude-sonnet-4-6") == "claude-sonnet-4-6"


def test_direct_anthropic_gateway_requires_its_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        direct_anthropic_gateway()


def test_direct_anthropic_gateway_injects_a_direct_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "eval-key")
    with patch("evals.anthropic_api.Anthropic") as client:
        gateway = direct_anthropic_gateway()
    client.assert_called_once_with(api_key="eval-key", timeout=180.0)
    assert gateway._client is client.return_value
