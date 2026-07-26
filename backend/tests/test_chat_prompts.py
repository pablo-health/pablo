# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the centralized chat prompts module.

Covers: OSS default is returned when no resolver is registered; a
registered resolver overrides the default; resetting clears the
override; the OSS default text contains the empty-chart safety
guidance that downstream consumers depend on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.prompts import chat as chat_prompts

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset_provider() -> Iterator[None]:
    """Ensure each test starts with a clean registry slot."""
    chat_prompts.reset_provider()
    yield
    chat_prompts.reset_provider()


def test_oss_default_returned_when_no_overlay() -> None:
    result = chat_prompts.get_chat_system_prompt()
    assert result == chat_prompts.DEFAULT_PROMPT


def test_oss_default_returned_for_any_provider_type_without_overlay() -> None:
    # OSS does not ship per-provider variants; same prompt regardless.
    for pt in (None, "therapist", "prescriber", "phpnp", "both"):
        assert chat_prompts.get_chat_system_prompt(pt) == chat_prompts.DEFAULT_PROMPT


def test_registered_resolver_overrides_default() -> None:
    chat_prompts.register_provider(lambda pt: f"OVERLAY for {pt}")
    assert chat_prompts.get_chat_system_prompt("therapist") == "OVERLAY for therapist"
    assert chat_prompts.get_chat_system_prompt("prescriber") == "OVERLAY for prescriber"
    assert chat_prompts.get_chat_system_prompt(None) == "OVERLAY for None"


def test_reset_provider_clears_overlay() -> None:
    chat_prompts.register_provider(lambda _pt: "OVERLAY")
    assert chat_prompts.get_chat_system_prompt() == "OVERLAY"
    chat_prompts.reset_provider()
    assert chat_prompts.get_chat_system_prompt() == chat_prompts.DEFAULT_PROMPT


def test_register_provider_is_idempotent() -> None:
    chat_prompts.register_provider(lambda _pt: "FIRST")
    chat_prompts.register_provider(lambda _pt: "SECOND")
    assert chat_prompts.get_chat_system_prompt() == "SECOND"


# ---------------------------------------------------------------------------
# Content assertions for the OSS default — these document the safety floor
# that downstream consumers MUST preserve in any overlay (motivated
# by a downstream production traceback).
# ---------------------------------------------------------------------------


def test_default_prompt_forbids_invention() -> None:
    """OSS default must instruct the model not to invent patient details."""
    body = chat_prompts.DEFAULT_PROMPT.lower()
    assert "never infer" in body or "do not infer" in body
    assert "extrapolate" in body or "invent" in body


def test_default_prompt_handles_empty_chart_case() -> None:
    """OSS default must explicitly tell the model what to do when the
    chart is entirely empty (downstream-motivated safety floor)."""
    body = chat_prompts.DEFAULT_PROMPT.lower()
    empty_chart_phrases = (
        "chart is empty",
        "chart contains no",
        "empty for the requested",
    )
    assert any(phrase in body for phrase in empty_chart_phrases)


def test_default_prompt_requests_citations() -> None:
    """OSS default must instruct the model to cite chart sources."""
    body = chat_prompts.DEFAULT_PROMPT.lower()
    assert "cite" in body
    # And specifically mentions at least one bracketed source name.
    assert "[intake]" in body or "[progress notes]" in body
