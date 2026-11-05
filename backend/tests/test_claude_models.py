# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a Claude model accepts is decided by the model, whichever endpoint names it."""

from __future__ import annotations

import pytest
from app.services.claude_models import ClaudeCapabilities, capabilities_for, sampling_kwargs

_ACCEPTS_ALL = ClaudeCapabilities(accepts_temperature=True, forces_tool_choice=True)
_NO_TEMPERATURE_FORCED_TOOL = ClaudeCapabilities(accepts_temperature=False, forces_tool_choice=True)
_NO_TEMPERATURE_AUTO_TOOL = ClaudeCapabilities(accepts_temperature=False, forces_tool_choice=False)


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        # Measured on Vertex 2026-11-05: Haiku 5.5 forces a tool, Sonnet and Opus do not.
        ("claude-haiku-5-5", _NO_TEMPERATURE_FORCED_TOOL),
        ("claude-sonnet-5-5", _NO_TEMPERATURE_AUTO_TOOL),
        ("claude-opus-5-5", _NO_TEMPERATURE_AUTO_TOOL),
        # A tier with no minor version.
        ("claude-sonnet-5", _NO_TEMPERATURE_AUTO_TOOL),
        ("claude-opus-5", _NO_TEMPERATURE_AUTO_TOOL),
        # The same model under each provider's spelling and prefix.
        ("anthropic:claude-sonnet-5-5", _NO_TEMPERATURE_AUTO_TOOL),
        ("bedrock:us.anthropic.claude-sonnet-5-5-v1:0", _NO_TEMPERATURE_AUTO_TOOL),
        ("us.anthropic.claude-haiku-5-5-20261005-v1:0", _NO_TEMPERATURE_FORCED_TOOL),
        ("claude-haiku-5-5@20261005", _NO_TEMPERATURE_FORCED_TOOL),
        # Models before the 5 family accept everything, as every gateway assumed.
        ("claude-sonnet-4-6", _ACCEPTS_ALL),
        ("bedrock:us.anthropic.claude-sonnet-4-6", _ACCEPTS_ALL),
        ("claude-haiku-4-5", _ACCEPTS_ALL),
        ("claude-haiku-4-5@20251001", _ACCEPTS_ALL),
        ("bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0", _ACCEPTS_ALL),
        ("claude-opus-4-8", _ACCEPTS_ALL),
        # Not a Claude id this module can place: leave the request as it was.
        ("gemini-3.1-pro-preview", _ACCEPTS_ALL),
        ("claude-3-5-sonnet-20241022", _ACCEPTS_ALL),
        ("", _ACCEPTS_ALL),
    ],
)
def test_capabilities_by_model(model: str, expected: ClaudeCapabilities) -> None:
    assert capabilities_for(model) == expected


def test_a_dated_id_is_not_read_as_a_later_version() -> None:
    """``claude-haiku-4-5-20251001`` is 4.5, not a 4.5-and-then-some that reaches 5."""
    assert capabilities_for("claude-haiku-4-5-20251001-v1:0").accepts_temperature


def test_sampling_kwargs_carries_temperature_only_where_it_is_accepted() -> None:
    assert sampling_kwargs("claude-sonnet-4-6", 0.4) == {"temperature": 0.4}
    assert sampling_kwargs("anthropic:claude-sonnet-5-5", 0.4) == {}
    assert sampling_kwargs("bedrock:us.anthropic.claude-sonnet-5-5-v1:0", 0.0) == {}


def test_a_zero_temperature_is_still_sent_where_accepted() -> None:
    """0.0 is a real setting (the deterministic calls), not an absent one."""
    assert sampling_kwargs("claude-haiku-4-5", 0.0) == {"temperature": 0.0}
