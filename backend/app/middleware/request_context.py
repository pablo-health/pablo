# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request-context middleware (THERAPY-2pf4).

Mints a request_id at the FastAPI boundary, resolves the matched route
template, and populates the contextvars consumed by the JSON formatter
in `app.logging_config`. The contextvars are reset on exit so they
don't leak across requests sharing the same worker thread.

Upstream trace correlation: if the request carries a recognized trace
header, we reuse the trace_id as our request_id so a single id pins a
log line to the upstream LB / sidecar / OTel-instrumented caller.
The same headers also give Cloud Logging its ``trace`` / ``spanId``
fields (see ``resolve_log_trace``), which nest every app line of a
request under that request's own log entry. For the request id, headers
are tried in priority order:

* ``traceparent`` — W3C Trace Context (the OpenTelemetry standard,
  emitted by any OTel-instrumented client and most modern proxies).
* ``X-Cloud-Trace-Context`` — Google Cloud LB / Cloud Run.
* ``X-Amzn-Trace-Id`` — AWS ALB / API Gateway.

If none are present (or all fail to parse), a fresh UUID4 is minted.
This keeps the middleware portable across clouds without any config.
"""

from __future__ import annotations

import contextlib
import logging
import time
import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from ..logging_config import (
    LogHttpRequest,
    LogTrace,
    begin_request_log_identity,
    end_request_log_identity,
    log_http_request_var,
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)
from ..route_introspection import iter_app_routes

# Dedicated logger for the per-request completion line. Distinct name
# (``pablo.access``) keeps the access log filterable in Cloud Logging
# without sweeping in app-level INFO records.
_access_logger = logging.getLogger("pablo.access")

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-Id"
W3C_TRACEPARENT_HEADER = "traceparent"
CLOUD_TRACE_HEADER = "X-Cloud-Trace-Context"
AWS_TRACE_HEADER = "X-Amzn-Trace-Id"

# W3C traceparent shape: <version>-<trace_id>-<parent_id>-<flags>
_W3C_TRACEPARENT_FIELDS = 4
_W3C_TRACE_ID_HEX_LEN = 32
_AWS_ROOT_PREFIX = "Root="


def _parse_w3c_traceparent(value: str) -> str | None:
    """W3C ``traceparent``: ``<version>-<32hex trace_id>-<16hex span_id>-<2hex flags>``.

    We only need trace_id. Reject anything that doesn't match the
    fixed shape so a stray header doesn't smuggle an arbitrary string
    into our log payload.
    """
    parts = value.strip().split("-")
    if len(parts) >= _W3C_TRACEPARENT_FIELDS and len(parts[1]) == _W3C_TRACE_ID_HEX_LEN:
        trace_id = parts[1]
        if all(c in "0123456789abcdef" for c in trace_id):
            return trace_id
    return None


def _parse_gcp_trace(value: str) -> str | None:
    """``X-Cloud-Trace-Context``: ``TRACE_ID/SPAN_ID;o=TRACE_TRUE``."""
    trace_id = value.split("/", 1)[0].split(";", 1)[0].strip()
    return trace_id or None


def _parse_aws_trace(value: str) -> str | None:
    """``X-Amzn-Trace-Id``: ``Root=1-<id>;Parent=...;Sampled=...``.

    AWS encodes the trace_id as the value of the ``Root=`` segment
    (other segments — Parent, Self, Calling, Sampled — are span
    metadata we don't need).
    """
    for raw in value.split(";"):
        segment = raw.strip()
        if segment.startswith(_AWS_ROOT_PREFIX):
            root = segment[len(_AWS_ROOT_PREFIX) :].strip()
            return root or None
    return None


# Priority order: W3C standard first (works everywhere OTel does),
# then cloud-specific fallbacks. The first parser to return non-None
# wins. Add new providers here rather than special-casing in dispatch.
_TRACE_PARSERS: tuple[tuple[str, Callable[[str], str | None]], ...] = (
    (W3C_TRACEPARENT_HEADER, _parse_w3c_traceparent),
    (CLOUD_TRACE_HEADER, _parse_gcp_trace),
    (AWS_TRACE_HEADER, _parse_aws_trace),
)


_HEX_DIGITS = frozenset("0123456789abcdef")
_SPAN_ID_HEX_LEN = 16
_MAX_SPAN_ID = 2**64 - 1


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and set(value) <= _HEX_DIGITS


def _is_id(value: str, length: int) -> bool:
    """A trace or span id: fixed-width lowercase hex, and not all zeros (invalid)."""
    return _is_hex(value, length) and value != "0" * length


def _gcp_log_trace(value: str) -> LogTrace | None:
    """``X-Cloud-Trace-Context`` as Cloud Logging's trace / spanId pair.

    The header's span id is decimal; ``LogEntry.spanId`` wants it as 16 hex
    characters. ``o=1`` is the sampled flag.
    """
    head, _, options = value.strip().partition(";")
    trace_id, _, span = head.partition("/")
    trace_id = trace_id.lower()
    if not _is_id(trace_id, _W3C_TRACE_ID_HEX_LEN):
        return None
    span_id: str | None = None
    if span.isdigit() and 0 < int(span) <= _MAX_SPAN_ID:
        span_id = f"{int(span):016x}"
    sampled: bool | None = None
    if options.startswith("o="):
        sampled = options[2:].strip() == "1"
    return LogTrace(trace_id=trace_id, span_id=span_id, sampled=sampled)


def _w3c_log_trace(value: str) -> LogTrace | None:
    """W3C ``traceparent`` as Cloud Logging's trace / spanId pair."""
    parts = value.strip().split("-")
    if len(parts) < _W3C_TRACEPARENT_FIELDS:
        return None
    trace_id, span_id, flags = parts[1], parts[2], parts[3]
    if not _is_id(trace_id, _W3C_TRACE_ID_HEX_LEN):
        return None
    return LogTrace(
        trace_id=trace_id,
        span_id=span_id if _is_id(span_id, _SPAN_ID_HEX_LEN) else None,
        sampled=int(flags, 16) & 1 == 1 if _is_hex(flags, 2) else None,
    )


# Google's request log takes its trace from ``X-Cloud-Trace-Context``, so it
# goes first here; ``traceparent`` covers a proxy that only speaks W3C. AWS's
# id is not a Cloud Trace id, so it never becomes one.
_LOG_TRACE_PARSERS: tuple[tuple[str, Callable[[str], LogTrace | None]], ...] = (
    (CLOUD_TRACE_HEADER, _gcp_log_trace),
    (W3C_TRACEPARENT_HEADER, _w3c_log_trace),
)


def resolve_log_trace(request: Request) -> LogTrace | None:
    """The request's trace, validated, for the log's trace / spanId fields."""
    for header, parser in _LOG_TRACE_PARSERS:
        value = request.headers.get(header)
        if value and (trace := parser(value)) is not None:
            return trace
    return None


def resolve_route_template(request: Request) -> str | None:
    """Return the matched route's path pattern, e.g. ``/api/patients/{id}``.

    Resolution is done by re-running Starlette's route matcher against
    the request scope. This is safe because matching is pure; it
    returns the literal route declaration, never a resolved path
    parameter (so PHI ids never end up in `route_template`).

    ``iter_app_routes`` flattens fastapi 0.137+'s nested route tree so we
    still see routes mounted via ``include_router`` (almost all of them),
    and reports each route's full path template rather than the
    router-relative one.
    """
    for path, route in iter_app_routes(request.app):
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            return path
    return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Populate request-scoped contextvars for the JSON log formatter.

    - Mints `request_id` (UUID4) or reuses X-Cloud-Trace-Context if set.
    - Resolves `route_template` from the matched route (pattern, not
      resolved values) so aggregations stay PHI-free.
    - Echoes the request_id on the response as ``X-Request-Id`` so
      clients (and downstream services) can pin a log line to a call.

    user_id / tenant_id are populated later, once the caller is known,
    through ``bind_log_user_id`` / ``bind_log_tenant_id`` (the database
    session middleware and the auth dependency chain).
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = self._derive_request_id(request)
        request.state.request_id = request_id

        rid_token = request_id_var.set(request_id)
        route_token = route_template_var.set(resolve_route_template(request))
        user_token = user_id_var.set(None)
        tenant_token = tenant_id_var.set(None)
        log_identity, log_identity_token = begin_request_log_identity()
        log_http_request = LogHttpRequest(method=request.method, trace=resolve_log_trace(request))
        http_request_token = log_http_request_var.set(log_http_request)
        # The unhandled-exception handler runs outside this middleware, after
        # the contextvars below are reset, so it reads the request's identity
        # and trace from here instead.
        request.state.log_identity = log_identity
        request.state.log_http_request = log_http_request

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            status_code = response.status_code
            return response
        finally:
            # Emit one `event=request_completed` record per request. Feeds
            # the 5xx-rate alert (THERAPY-8uww) and the per-route latency
            # widgets on the ops dashboard. PHI-free — only the HTTP
            # method, the matched route template (never the resolved URL),
            # the status code, and the elapsed time. route_template,
            # request_id, user_id, and tenant_id are merged in by the
            # JSON formatter via contextvars.
            latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
            # Access logging must never break a response — swallow any
            # exception out of the finally block rather than letting it
            # escape and mask the real error.
            with contextlib.suppress(Exception):
                _access_logger.info(
                    "request_completed",
                    extra={
                        "event": "request_completed",
                        "method": request.method,
                        "status_code": status_code,
                        "latency_ms": latency_ms,
                    },
                )
            request_id_var.reset(rid_token)
            route_template_var.reset(route_token)
            user_id_var.reset(user_token)
            tenant_id_var.reset(tenant_token)
            log_http_request_var.reset(http_request_token)
            end_request_log_identity(log_identity_token)

    @staticmethod
    def _derive_request_id(request: Request) -> str:
        for header, parser in _TRACE_PARSERS:
            value = request.headers.get(header)
            if value:
                parsed = parser(value)
                if parsed:
                    return parsed
        return str(uuid.uuid4())
