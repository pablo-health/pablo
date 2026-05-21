# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for GeminiChatLLMGateway model-name normalization."""

from __future__ import annotations

import logging

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
