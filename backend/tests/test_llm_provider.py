"""Unit tests for provider-prefix handling."""

from __future__ import annotations

import pytest

from backend.app.services.llm_provider import LLMProvider, strip_provider_prefix


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("google:gemini-3.1-pro", "gemini-3.1-pro"),
        ("google:gemini-2.5-flash", "gemini-2.5-flash"),
        ("gemini-3.1-pro", "gemini-3.1-pro"),  # bare id unchanged
        ("openai:gpt-4", "openai:gpt-4"),  # unknown prefix unchanged
        ("gemini:1.5", "gemini:1.5"),  # colon but not a known provider
        ("", ""),
    ],
)
def test_strip_provider_prefix(model: str, expected: str) -> None:
    assert strip_provider_prefix(model) == expected


def test_known_provider_value() -> None:
    assert LLMProvider.GOOGLE == "google"
    assert str(LLMProvider.GOOGLE) == "google"
