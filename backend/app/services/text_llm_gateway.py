# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""One-shot Gemini text generation through the shared gateway seam.

The structured gateway (:mod:`structured_llm_gateway`) returns parsed JSON
for a JSON-schema contract. A caller that wants the model's text back as is
(a written report, a drafted message) or that hands the SDK its own schema
object has no such contract to parse, but it should still get the same
treatment as every other model call: one content-free ``llm_call`` record
labelled with the feature it served, the shared retry engine, the Vertex
client built in one place, and the Vertex location default.

This module is that path. It owns the ``generate_content`` call for those
callers so none of them builds an SDK client of its own.

Optional prompt caching
-----------------------
``cache_system_prompt=True`` asks Vertex to hold the system prompt in an
explicit context cache for ``cache_ttl_seconds`` and reuse it on later calls
with the same model and prompt. It is off by default and never required: a
prompt too short to cache, a quota error, or any other cache failure falls
back to sending the prompt inline, so enabling it can change cost but not
the answer. Implicit caching, which Vertex applies on its own to repeated
prompt prefixes, needs nothing here and shows up as ``llm_cached_tokens`` on
the usage record either way.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from typing import Any

from ..reliability import LLM_JOB, Idempotency, RetryPolicy, call_with_retry
from .llm_provider import strip_provider_prefix
from .llm_telemetry import (
    LLMSpanRequest,
    llm_feature,
    llm_span,
    usage_detail_tokens,
    usage_tokens,
)
from .vertex_client import DEFAULT_VERTEX_TIMEOUT_SECONDS, vertex_genai_client

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 3600

# A cache entry is refreshed this long before Vertex would drop it, so a call
# never points at a cache that expires mid-request.
_CACHE_REFRESH_MARGIN_SECONDS = 120.0

# After a failed create (prompt under the provider minimum, quota, ...) the
# same prompt is not retried for this long: the call goes inline instead of
# paying a failing round-trip on every request.
_CACHE_FAILURE_BACKOFF_SECONDS = 600.0


def resolve_location() -> str:
    """The Vertex location, defaulting to ``global`` (where Gemini 3.x serves)."""
    return os.environ.get("GOOGLE_CLOUD_LOCATION", "global")


class _CacheEntry:
    __slots__ = ("expires_at", "name")

    def __init__(self, name: str | None, expires_at: float) -> None:
        self.name = name  # None records a recent failure
        self.expires_at = expires_at


_cache_lock = threading.Lock()
_caches: dict[tuple[str, str], _CacheEntry] = {}


def _cached_content_name(
    client: Any, types: Any, *, model: str, system_prompt: str, ttl_seconds: int
) -> str | None:
    """The name of a live context cache holding ``system_prompt``, or ``None``.

    Keyed by model and a hash of the prompt, so two prompts never share an
    entry and the prompt text itself is never stored as a key. Returns
    ``None`` whenever a cache cannot be had; the caller then sends the prompt
    inline.
    """
    key = (model, hashlib.sha256(system_prompt.encode("utf-8")).hexdigest())
    now = time.monotonic()
    with _cache_lock:
        entry = _caches.get(key)
        if entry is not None and entry.expires_at - _CACHE_REFRESH_MARGIN_SECONDS > now:
            return entry.name
        if entry is not None and entry.name is None and entry.expires_at > now:
            return None
    try:
        created = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                system_instruction=system_prompt, ttl=f"{ttl_seconds}s"
            ),
        )
    except Exception as exc:
        # The class name only: an SDK error message can echo request content.
        logger.info("prompt cache unavailable, sending inline: %s", type(exc).__name__)
        with _cache_lock:
            _caches[key] = _CacheEntry(None, now + _CACHE_FAILURE_BACKOFF_SECONDS)
        return None
    name = getattr(created, "name", None)
    with _cache_lock:
        _caches[key] = _CacheEntry(name, now + ttl_seconds)
    return name if isinstance(name, str) else None


def generate_text(
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    temperature: float,
    max_output_tokens: int,
    response_mime_type: str | None = None,
    response_schema: Any = None,
    policy: RetryPolicy = LLM_JOB,
    timeout_seconds: float = DEFAULT_VERTEX_TIMEOUT_SECONDS,
    feature: str | None = None,
    cache_system_prompt: bool = False,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    client: Any = None,
) -> str:
    """Run one Gemini ``generate_content`` call and return its text.

    ``model`` may carry a ``provider:`` prefix; it is stripped. ``feature``
    labels the usage record. ``response_mime_type`` and ``response_schema``
    are forwarded only when set. ``client`` lets a caller that already holds
    a client reuse it; otherwise one is built here.

    A run that never succeeds raises
    :class:`backend.app.reliability.RetryExhaustedError` wrapping the last
    failure; setup errors (auth, missing SDK) propagate.
    """
    try:
        from google.genai import types
    except ImportError:
        logger.exception("google-genai is required for text generation.")
        raise

    normalized = strip_provider_prefix(model)
    sdk = client if client is not None else vertex_genai_client(timeout_seconds=timeout_seconds)
    config_kwargs: dict[str, Any] = {
        "temperature": temperature,
        "max_output_tokens": max_output_tokens,
    }
    cache_name = (
        _cached_content_name(
            sdk, types, model=normalized, system_prompt=system_prompt, ttl_seconds=cache_ttl_seconds
        )
        if cache_system_prompt
        else None
    )
    if cache_name is not None:
        config_kwargs["cached_content"] = cache_name
    else:
        config_kwargs["system_instruction"] = system_prompt
    if response_mime_type is not None:
        config_kwargs["response_mime_type"] = response_mime_type
    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema

    def _call() -> Any:
        return sdk.models.generate_content(
            model=normalized,
            contents=user_content,
            config=types.GenerateContentConfig(**config_kwargs),
        )

    with (
        llm_feature(feature),
        llm_span(LLMSpanRequest(operation="text", model=normalized)) as span,
    ):
        resp = call_with_retry(_call, policy=policy, idempotency=Idempotency.SAFE)
        usage = getattr(resp, "usage_metadata", None)
        prompt_tokens, completion_tokens, total_tokens = usage_tokens(usage)
        cached_tokens, thoughts_tokens = usage_detail_tokens(usage)
        span.set_token_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
            thoughts_tokens=thoughts_tokens,
        )
    return (resp.text or "").strip()
