# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression guard: ``google.genai`` must be installable.

Every Gemini-backed surface in pablo (ChatLLMGateway, StructuredLLMGateway,
EhrNavigationService, EmbeddingService) imports ``from google import genai``
*lazily* inside its ``_get_client()`` method. A missing dep therefore won't
fail container startup or unit tests (which use fakes) — it fails the first
real Gemini call, in production.

This test fails at import time if the SDK isn't installed, so we catch a
regression like THERAPY-71d5's accidental drop of ``google-genai`` (was
transitive via meeting-transcription's optional extra) before it ships.
"""

from __future__ import annotations


def test_google_genai_sdk_is_installed() -> None:
    """The vendored SDK every gateway depends on must be available."""
    from google import genai  # noqa: PLC0415 — the test is exactly this import

    # Touch a top-level symbol so a partial install / namespace-package
    # collision can't slip through.
    assert hasattr(genai, "Client")
