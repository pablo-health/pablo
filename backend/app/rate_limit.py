# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Rate limiting for pre-auth and per-user endpoints.

Two implementations:
- InMemorySlidingWindow: process-local, for single-instance / self-hosted
- RedisSlidingWindow: shared across instances, for multi-instance Cloud Run

Both expose the same check(key) interface. The factory selects based on USE_REDIS.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from threading import Lock
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import redis

from fastapi import HTTPException, Request, status

logger = logging.getLogger(__name__)

_TOO_MANY_REQUESTS = HTTPException(
    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
    detail="Too many requests. Please try again later.",
)


class RateLimiter(Protocol):
    """Protocol for rate limiters."""

    def check(self, key: str) -> None: ...
    def reset(self) -> None: ...


class InMemorySlidingWindow:
    """Thread-safe in-memory sliding window rate limiter.

    max_keys caps tracked IPs to prevent unbounded memory growth.
    """

    def __init__(
        self, max_requests: int, window_seconds: int, *, max_keys: int = 10_000
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._max_keys = max_keys
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            if len(self._hits) >= self._max_keys and key not in self._hits:
                stale = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
                for k in stale:
                    del self._hits[k]

            self._hits[key] = [t for t in self._hits[key] if t > cutoff]
            if len(self._hits[key]) >= self.max_requests:
                raise _TOO_MANY_REQUESTS
            self._hits[key].append(now)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


class RedisSlidingWindow:
    """Redis-backed sliding window rate limiter using sorted sets.

    Each key is a sorted set where members are unique request IDs
    and scores are timestamps. On each check:
    1. Remove entries outside the window
    2. Count remaining entries
    3. If under limit, add the new entry
    """

    KEY_PREFIX = "ratelimit:"

    def __init__(self, max_requests: int, window_seconds: int, redis_client: redis.Redis) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis = redis_client

    def check(self, key: str) -> None:
        now = time.time()
        cutoff = now - self.window_seconds
        rkey = f"{self.KEY_PREFIX}{key}"

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, cutoff)
        pipe.zcard(rkey)
        pipe.zadd(rkey, {f"{now}": now})
        pipe.expire(rkey, self.window_seconds + 1)
        results = pipe.execute()

        count = results[1]  # zcard result (before adding new entry)
        if count >= self.max_requests:
            # Remove the entry we just added since we're over limit
            self._redis.zrem(rkey, f"{now}")
            raise _TOO_MANY_REQUESTS

    def reset(self) -> None:
        # Scan and delete all rate limit keys
        for rkey in self._redis.scan_iter(f"{self.KEY_PREFIX}*"):
            self._redis.delete(rkey)


def _create_limiter(max_requests: int, window_seconds: int) -> RateLimiter:
    """Create the appropriate limiter based on settings."""
    from .redis_client import get_redis_client  # noqa: PLC0415

    client = get_redis_client()
    if client is not None:
        return RedisSlidingWindow(max_requests, window_seconds, client)
    return InMemorySlidingWindow(max_requests, window_seconds)


# Pre-auth endpoints: 10 requests per 60 seconds per IP
_preauth_limiter: RateLimiter | None = None


def _get_preauth_limiter() -> RateLimiter:
    global _preauth_limiter  # noqa: PLW0603
    if _preauth_limiter is None:
        _preauth_limiter = _create_limiter(max_requests=10, window_seconds=60)
        logger.info("Pre-auth rate limiter: %s", type(_preauth_limiter).__name__)
    return _preauth_limiter


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP, using X-Forwarded-For when behind a proxy.

    Cloud Run always sets X-Forwarded-For with the real client IP as the
    first entry. We use it when present to avoid rate-limiting all users
    collectively via the load balancer IP.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_rate_limit(request: Request) -> None:
    """FastAPI dependency that enforces rate limiting by client IP."""
    _get_preauth_limiter().check(_get_client_ip(request))


# EHR navigate: per-user daily rate limit (lazily initialized from settings)
_ehr_navigate_limiter: RateLimiter | None = None


def get_ehr_navigate_limiter() -> RateLimiter:
    """Get the per-user daily rate limiter for EHR navigate endpoint."""
    global _ehr_navigate_limiter  # noqa: PLW0603
    if _ehr_navigate_limiter is None:
        from .settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        _ehr_navigate_limiter = _create_limiter(
            max_requests=settings.ehr_navigate_daily_limit,
            window_seconds=86_400,
        )
        logger.info("EHR navigate rate limiter: %s", type(_ehr_navigate_limiter).__name__)
    return _ehr_navigate_limiter


def reset_preauth_limiter() -> None:
    """Reset the pre-auth rate limiter. Used by tests."""
    _get_preauth_limiter().reset()
