# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A general per-address request ceiling across the whole API.

The limiters in ``app.rate_limit`` guard the sensitive endpoints (sign-in,
recovery, the portal, public booking). None of them bounds how much a single
address can call everything else, so a deployment with no edge rule for it has
nothing to stop one caller with a valid session hammering the API.

This middleware is that ceiling. It keys on the client address from
``app.rate_limit.get_client_ip`` (the same trusted-proxy reading the other
limiters use) and counts in the shared limiter (Redis when configured,
in-memory otherwise).

It is registered OUTSIDE the database session middleware, so a refused request
is answered before a session is opened or a pooled connection is drawn: a flood
costs the database nothing.

Off unless ``general_ip_rate_limit_per_min`` is above 0.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from ..logging_config import record_log_outcome
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


def _parse_exempt(raw: str) -> frozenset[str]:
    return frozenset(ip.strip() for ip in raw.split(",") if ip.strip())


class GeneralIPRateLimitMiddleware(BaseHTTPMiddleware):
    """Answer 429 once one address passes the per-minute ceiling on any route."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        super().__init__(app)
        self._max_per_min = settings.general_ip_rate_limit_per_min
        self._exempt = _parse_exempt(settings.general_ip_rate_limit_exempt_ips)
        # Built on first use: the Redis client is resolved at request time,
        # like every other limiter, not when the app module is imported.
        self._limiter: RateLimiter | None = None

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
            logger.warning(
                "General request limit could not be checked; request allowed (%s)",
                type(exc).__name__,
            )
        return False

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if self._max_per_min <= 0 or request.url.path in PLATFORM_PROBE_PATHS:
            return await call_next(request)

        from ..rate_limit import get_client_ip  # noqa: PLC0415

        client = get_client_ip(request)
        if client in self._exempt:
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
