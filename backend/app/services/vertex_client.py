"""Construct the Vertex AI clients (google-genai for Gemini, anthropic for Claude).

The single place the Vertex clients are built. google-genai reads the project and
location from the ambient ``GOOGLE_CLOUD_*`` environment, so callers pass
nothing — this centralizes the import guard and the ``vertexai=True`` flag that
every genai-backed service would otherwise repeat in its own ``_get_client``.
The Anthropic publisher models on Vertex are served from a separate client and a
fixed regional endpoint, so they get their own factory here.

Both factories also default the client's own per-call timeout. This is the
"part (a)" half of adopting the reliability engine (see
``backend/app/reliability/retry.py``): the engine's retry/backoff loop only
bounds attempt *count*, never how long a single blocking call may run — that
has to come from the SDK's own deadline, set here.
"""

from __future__ import annotations

import os
from typing import Any

# Anthropic publisher models on Vertex are served from the ``global`` endpoint;
# the Gemini region (``GOOGLE_CLOUD_LOCATION``) often differs and would 404 for
# Claude, so default independently and allow an explicit override.
_ANTHROPIC_VERTEX_REGION = os.environ.get("ANTHROPIC_VERTEX_REGION", "global")

# Per-call deadline for a single Vertex request, both request-path and
# job/cron callers. The same 60 s the request-path preset names as its own
# per-attempt bound (``reliability.LLM_REQUEST.attempt_timeout``), of which
# two fit inside that preset's deadline with the backoff between them, so a
# request-path caller on this default can retry a call that failed at its
# bound. Generous for an ordinary answer because a streaming chat completion
# or a large structured extraction can legitimately run long; this exists to
# fail a truly hung connection, not to shape p99 latency.
DEFAULT_VERTEX_TIMEOUT_SECONDS = 60.0


def seconds_to_genai_timeout_ms(seconds: float) -> int:
    """Convert a seconds-based timeout to the milliseconds ``HttpOptions`` wants.

    ``google.genai.types.HttpOptions.timeout`` is documented and implemented
    in milliseconds (the SDK divides by 1000 before handing it to httpx) —
    unlike every other timeout in this module, which is seconds. Centralized
    here so no call site has to remember the unit mismatch.
    """
    return int(seconds * 1000)


def vertex_genai_client(*, timeout_seconds: float = DEFAULT_VERTEX_TIMEOUT_SECONDS) -> Any:
    """Return a new google-genai client configured for Vertex AI.

    Imported lazily so units that never touch Vertex don't pay the import cost.

    Raises:
        RuntimeError: google-genai is not installed.
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai package is required for Vertex AI access") from exc
    http_options = types.HttpOptions(timeout=seconds_to_genai_timeout_ms(timeout_seconds))
    return genai.Client(vertexai=True, http_options=http_options)


def anthropic_vertex_client(
    *, region: str | None = None, timeout_seconds: float = DEFAULT_VERTEX_TIMEOUT_SECONDS
) -> Any:
    """Return a new anthropic client bound to Claude on Vertex AI.

    Project comes from ``ANTHROPIC_VERTEX_PROJECT_ID`` when set, else
    ``GOOGLE_CLOUD_PROJECT``. The separate variable lets a deployment serve Claude
    from a different project than the one Gemini, Document AI and logging use,
    without redirecting those. The region defaults to ``ANTHROPIC_VERTEX_REGION``
    (``global``) rather than the Gemini location, since the Anthropic publisher
    models are served from a different endpoint. Imported lazily so units that
    never touch Claude don't pay the import cost.

    Raises:
        RuntimeError: the anthropic package is not installed.
    """
    try:
        from anthropic import NOT_GIVEN, AnthropicVertex
    except ImportError as exc:
        raise RuntimeError("anthropic package is required for Claude on Vertex AI access") from exc
    # project_id/region take a NotGiven sentinel (not None) when unset; with no
    # project the client falls back to the ambient GOOGLE_CLOUD_PROJECT itself.
    return AnthropicVertex(
        project_id=_claude_project() or NOT_GIVEN,
        region=region or _ANTHROPIC_VERTEX_REGION,
        timeout=timeout_seconds,
        max_retries=_SDK_MAX_RETRIES,
    )


def async_anthropic_vertex_client(
    *, region: str | None = None, timeout_seconds: float = DEFAULT_VERTEX_TIMEOUT_SECONDS
) -> Any:
    """Return a new async anthropic client bound to Claude on Vertex AI.

    The streaming sibling of :func:`anthropic_vertex_client`: the same project,
    region and retry policy, for callers that stream an answer on the event
    loop. ``timeout_seconds`` bounds each wait on the connection, not the
    whole answer.

    Raises:
        RuntimeError: the anthropic package is not installed.
    """
    try:
        from anthropic import NOT_GIVEN, AsyncAnthropicVertex
    except ImportError as exc:
        raise RuntimeError("anthropic package is required for Claude on Vertex AI access") from exc
    return AsyncAnthropicVertex(
        project_id=_claude_project() or NOT_GIVEN,
        region=region or _ANTHROPIC_VERTEX_REGION,
        timeout=timeout_seconds,
        max_retries=_SDK_MAX_RETRIES,
    )


def _claude_project() -> str | None:
    """The project Claude is called against: its own variable, else the ambient one."""
    return os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")


# The SDK's own retry loop (default 2) would otherwise stack with the
# reliability engine's attempts — up to 3x SDK tries per engine attempt, with
# the SDK's internal backoff and retried reads able to run one engine attempt
# past LLM_REQUEST's attempt bound and eat the deadline from inside it. Mirrors
# `retry=None` on the Document AI gax client: this module owns the retry policy.
_SDK_MAX_RETRIES = 0
