# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Outbound-call trace propagation.

The inbound side (`request_context.py`) drops a `request_id` into a
contextvar. This module exposes a counterpart for outbound HTTP calls:
an httpx event hook that injects the current `request_id` onto every
outgoing request as `X-Request-Id` and (when the id is W3C-shaped) as
the standard `traceparent` header.

Use `tracing_async_client(...)` in place of `httpx.AsyncClient(...)`
and any outbound call made through it will carry correlation headers
automatically — no per-call-site work, nothing to forget.

Trade-off vs OpenTelemetry: this is correlation only (a single id end
to end), not span propagation (no parent_id, no sampling, no per-call
timing). If we ever need real distributed tracing, replace this with
`opentelemetry-instrumentation-httpx`; the call-site API
(`tracing_async_client`) is small and easy to swap.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import httpx

from ..logging_config import request_id_var
from .request_context import REQUEST_ID_HEADER, W3C_TRACEPARENT_HEADER

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_HEX = frozenset("0123456789abcdef")
_W3C_TRACE_ID_HEX_LEN = 32
_W3C_SPAN_ID_HEX_LEN = 16


def _trace_id_for_w3c(request_id: str) -> str | None:
    """Coerce a request_id into a W3C-compatible 32-hex trace_id, or None.

    Handles the two shapes we mint or accept upstream:
      * UUID4 with dashes (``str(uuid.uuid4())``) — strip dashes.
      * Already-32-hex (GCP X-Cloud-Trace-Context, W3C traceparent
        passed through).

    Anything else (e.g. an AWS X-Amzn-Trace-Id Root value like
    ``1-67891233-...``) returns None so we don't ship a malformed
    traceparent. Callers still get `X-Request-Id` as a portable
    fallback.
    """
    candidate = request_id.replace("-", "").lower()
    if len(candidate) == _W3C_TRACE_ID_HEX_LEN and all(c in _HEX for c in candidate):
        return candidate
    return None


def build_traceparent(request_id: str) -> str | None:
    """Build a W3C ``traceparent`` value for the given request_id.

    Returns None when the id can't be expressed as a 32-hex trace_id.
    The span_id is freshly minted per call because we don't track
    spans here — downstream services see this as the root span of
    whatever trace they're joining.
    """
    trace_id = _trace_id_for_w3c(request_id)
    if trace_id is None:
        return None
    span_id = secrets.token_hex(_W3C_SPAN_ID_HEX_LEN // 2)
    # version=00, flags=01 (sampled). We don't make a sampling decision
    # here, but marking sampled=true matches what an upstream LB would
    # set when it forwards the call to us.
    return f"00-{trace_id}-{span_id}-01"


async def inject_trace_headers(request: httpx.Request) -> None:
    """httpx request event hook — set correlation headers if a request_id is bound.

    No-op when called outside a request scope (e.g. from a startup
    task or a background poller that hasn't restored context).
    `setdefault` lets explicit caller-set headers win, so a service
    propagating an upstream trace verbatim isn't overwritten.
    """
    request_id = request_id_var.get()
    if request_id is None:
        return

    if REQUEST_ID_HEADER not in request.headers:
        request.headers[REQUEST_ID_HEADER] = request_id

    if W3C_TRACEPARENT_HEADER not in request.headers:
        traceparent = build_traceparent(request_id)
        if traceparent is not None:
            request.headers[W3C_TRACEPARENT_HEADER] = traceparent


def tracing_async_client(**kwargs: Any) -> httpx.AsyncClient:
    """Return an `httpx.AsyncClient` that injects correlation headers.

    Drop-in for `httpx.AsyncClient(...)`. Caller-supplied
    `event_hooks` are preserved; our hook is appended to the
    `request` hook list so it runs after any caller hooks.
    """
    hooks: dict[str, list[Callable[..., Awaitable[None]]]] = dict(
        kwargs.pop("event_hooks", {}) or {}
    )
    request_hooks = list(hooks.get("request", []))
    request_hooks.append(inject_trace_headers)
    hooks["request"] = request_hooks
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
