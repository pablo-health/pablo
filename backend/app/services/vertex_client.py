"""Construct the Vertex AI clients (google-genai for Gemini, anthropic for Claude).

The single place the Vertex clients are built. google-genai reads the project and
location from the ambient ``GOOGLE_CLOUD_*`` environment, so callers pass
nothing — this centralizes the import guard and the ``vertexai=True`` flag that
every genai-backed service would otherwise repeat in its own ``_get_client``.
The Anthropic publisher models on Vertex are served from a separate client and a
fixed regional endpoint, so they get their own factory here.
"""

from __future__ import annotations

import os
from typing import Any

# Anthropic publisher models on Vertex are served from the ``global`` endpoint;
# the Gemini region (``GOOGLE_CLOUD_LOCATION``) often differs and would 404 for
# Claude, so default independently and allow an explicit override.
_ANTHROPIC_VERTEX_REGION = os.environ.get("ANTHROPIC_VERTEX_REGION", "global")


def vertex_genai_client() -> Any:
    """Return a new google-genai client configured for Vertex AI.

    Imported lazily so units that never touch Vertex don't pay the import cost.

    Raises:
        RuntimeError: google-genai is not installed.
    """
    try:
        from google import genai
    except ImportError as exc:
        raise RuntimeError("google-genai package is required for Vertex AI access") from exc
    return genai.Client(vertexai=True)


def anthropic_vertex_client(*, region: str | None = None) -> Any:
    """Return a new anthropic client bound to Claude on Vertex AI.

    Project comes from ``GOOGLE_CLOUD_PROJECT``; the region defaults to
    ``ANTHROPIC_VERTEX_REGION`` (``global``) rather than the Gemini location, since
    the Anthropic publisher models are served from a different endpoint. Imported
    lazily so units that never touch Claude don't pay the import cost.

    Raises:
        RuntimeError: the anthropic package is not installed.
    """
    try:
        from anthropic import NOT_GIVEN, AnthropicVertex
    except ImportError as exc:
        raise RuntimeError("anthropic package is required for Claude on Vertex AI access") from exc
    # project_id/region take a NotGiven sentinel (not None) when unset; with no
    # project the client falls back to the ambient GOOGLE_CLOUD_PROJECT itself.
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    return AnthropicVertex(
        project_id=project or NOT_GIVEN,
        region=region or _ANTHROPIC_VERTEX_REGION,
    )
