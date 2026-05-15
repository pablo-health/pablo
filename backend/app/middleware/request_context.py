# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request-context middleware (THERAPY-2pf4).

Mints a request_id at the FastAPI boundary, resolves the matched route
template, and populates the contextvars consumed by the JSON formatter
in `app.logging_config`. The contextvars are reset on exit so they
don't leak across requests sharing the same worker thread.

X-Cloud-Trace-Context format (set by Google Cloud Load Balancer /
Cloud Run): ``TRACE_ID/SPAN_ID;o=TRACE_TRUE``. When present, we adopt
the trace_id as our request_id so a single id correlates logs across
the LB, our app, and any downstream Google services. Otherwise we mint
a fresh UUID4.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match

from ..logging_config import (
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-Id"
CLOUD_TRACE_HEADER = "X-Cloud-Trace-Context"


def _request_id_from_trace(trace_header: str) -> str | None:
    """Extract the trace_id portion of an X-Cloud-Trace-Context header.

    Returns None for malformed input. The trace_id is the substring
    before the first '/' (the span_id) or ';' (the options block).
    """
    trace_id = trace_header.split("/", 1)[0].split(";", 1)[0].strip()
    return trace_id or None


def resolve_route_template(request: Request) -> str | None:
    """Return the matched route's path pattern, e.g. ``/api/patients/{id}``.

    Resolution is done by re-running Starlette's route matcher against
    the request scope. This is safe because matching is pure; it
    returns the literal route declaration, never a resolved path
    parameter (so PHI ids never end up in `route_template`).
    """
    for route in request.app.router.routes:
        match, _ = route.matches(request.scope)
        if match == Match.FULL:
            path = getattr(route, "path", None)
            if isinstance(path, str):
                return path
    return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Populate request-scoped contextvars for the JSON log formatter.

    - Mints `request_id` (UUID4) or reuses X-Cloud-Trace-Context if set.
    - Resolves `route_template` from the matched route (pattern, not
      resolved values) so aggregations stay PHI-free.
    - Echoes the request_id on the response as ``X-Request-Id`` so
      clients (and downstream services) can pin a log line to a call.

    user_id / tenant_id are populated later by the auth dependency
    chain (see `app.auth.service._set_log_identity_context`).
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

        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(rid_token)
            route_template_var.reset(route_token)
            user_id_var.reset(user_token)
            tenant_id_var.reset(tenant_token)

    @staticmethod
    def _derive_request_id(request: Request) -> str:
        trace = request.headers.get(CLOUD_TRACE_HEADER)
        if trace:
            parsed = _request_id_from_trace(trace)
            if parsed:
                return parsed
        return str(uuid.uuid4())
