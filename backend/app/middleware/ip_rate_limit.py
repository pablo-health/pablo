# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A general per-address request ceiling across the whole API.

The limiters in ``app.rate_limit`` guard the sensitive endpoints (sign-in,
recovery, the portal, public booking). None of them bounds how much a single
address can call everything else, so a deployment with no edge rule for it has
nothing to stop one caller with a valid session hammering the API.

This middleware is that ceiling. It counts in the shared limiter (Redis when
configured, in-memory otherwise), keyed on the client address:

* the trusted-proxy reading the other limiters use (``trusted_proxy.client_ip``):
  the peer, or the right end of X-Forwarded-For behind a trusted proxy;
* except for a call the web app's own server makes, proven by the forward
  secret (``request_context``). Those all come from one address, the web app
  server's, on behalf of every visitor: a practice website's pages and assets,
  host lookups, server-rendered pages. Keyed on that address they would share
  one budget, and one busy website would take every practice's down with it.
  So such a call is counted against the visitor address the server forwards,
  and a call that names no visitor (the server's own cached lookups) is not
  counted here. The per-route limits still apply to it.

It is registered OUTSIDE the database session middleware, so a refused request
is answered before a session is opened or a pooled connection is drawn: a flood
costs the database nothing.

Off unless ``general_ip_rate_limit_per_min`` is above 0.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from ..logging_config import record_log_outcome
from ..request_context import forwarded_client_ip, is_from_web_app_server, parse_forward_secrets
from ..trusted_proxy import client_ip
from .security import PLATFORM_PROBE_PATHS

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import Request, Response
    from starlette.types import ASGIApp

    from ..rate_limit import RateLimiter
    from ..settings import Settings

logger = logging.getLogger(__name__)

#: The window the per-minute ceiling counts over, and so the longest a refused
#: caller has to wait before the oldest request in it drops out.
WINDOW_SECONDS = 60

#: The ``error_code`` a refused request's access log line carries.
RATE_LIMITED_ERROR_CODE = "GENERAL_IP_RATE_LIMITED"

#: At most one "could not be checked" warning per this many seconds, so a
#: Redis outage logs a line a minute rather than a line per request.
UNCHECKED_WARNING_INTERVAL_SECONDS = 60.0


def _parse_exempt(raw: str) -> frozenset[str]:
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


class GeneralIPRateLimitMiddleware(BaseHTTPMiddleware):
    """Answer 429 once one address passes the per-minute ceiling on any route."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self._settings = settings
        self._forward_secrets = parse_forward_secrets(
            settings.frontend_forward_secret.get_secret_value()
        )
        self._max_per_min = settings.general_ip_rate_limit_per_min
        self._exempt = _parse_exempt(settings.general_ip_rate_limit_exempt_ips)
        # Built on first use: the Redis client is resolved at request time,
        # like every other limiter, not when the app module is imported.
        self._limiter: RateLimiter | None = None
        self._last_unchecked_warning: float | None = None

    def _get_limiter(self) -> RateLimiter:
        if self._limiter is None:
            from ..rate_limit import _create_windowed_limiter  # noqa: PLC0415

            self._limiter = _create_windowed_limiter(
                "general-ip", self._max_per_min, WINDOW_SECONDS
            )
        return self._limiter

    def _is_refused(self, key: str) -> bool:
        """True when this request takes the address over the ceiling.

        A limiter that cannot answer (Redis unreachable) lets the request
        through: this ceiling is a guard on top of the API, and failing closed
        would turn a cache outage into an outage of every route.
        """
        try:
            self._get_limiter().check(key)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                return True
            raise
        except Exception as exc:
            now = time.monotonic()
            last = self._last_unchecked_warning
            if last is None or now - last >= UNCHECKED_WARNING_INTERVAL_SECONDS:
                self._last_unchecked_warning = now
                logger.warning(
                    "General request limit could not be checked; requests allowed (%s)",
                    type(exc).__name__,
                )
        return False

    def _client_key(self, request: Request) -> str | None:
        """The address this request counts against, or None when it is not counted.

        See the module docstring: a call from the web app's server counts
        against the visitor it forwards, and one naming no visitor is not
        counted.
        """
        if is_from_web_app_server(request, self._forward_secrets):
            return forwarded_client_ip(request)
        return client_ip(request, self._settings) or "unknown"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if self._max_per_min <= 0 or request.url.path in PLATFORM_PROBE_PATHS:
            return await call_next(request)

        client = self._client_key(request)
        if client is None or client in self._exempt:
            return await call_next(request)

        # The Redis limiter makes a blocking network round trip; keep it off
        # the event loop.
        if not await run_in_threadpool(self._is_refused, client):
            return await call_next(request)

        # No path and no address here: the JSON formatter adds the matched
        # route's pattern (never the resolved path, which carries record ids)
        # from the request context, and the access line carries the rest.
        record_log_outcome(error_code=RATE_LIMITED_ERROR_CODE)
        logger.warning(
            "General request limit reached; request refused",
            extra={"event": "general_ip_rate_limited"},
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"detail": "Too many requests. Please try again later."},
            headers={"Retry-After": str(WINDOW_SECONDS)},
        )
