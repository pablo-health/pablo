"""Construct the google-genai client bound to Vertex AI.

The single place the Vertex client is built. google-genai reads the project and
location from the ambient ``GOOGLE_CLOUD_*`` environment, so callers pass
nothing — this centralizes the import guard and the ``vertexai=True`` flag that
every genai-backed service would otherwise repeat in its own ``_get_client``.
"""

from __future__ import annotations

from typing import Any


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
