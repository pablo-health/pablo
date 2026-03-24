# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Simple in-memory rate limiter for pre-auth endpoints.

Uses a sliding window counter per client IP.
Sufficient for single-instance deployments; for multi-instance,
swap the in-memory store for Redis.
"""

import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request, status


class _SlidingWindow:
    """Thread-safe sliding window rate limiter.

    max_keys caps the number of tracked IPs to prevent unbounded memory growth
    under sustained attacks with many unique source IPs.
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
        """Raise 429 if the key has exceeded the rate limit."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            # Evict stale keys if at capacity
            if len(self._hits) >= self._max_keys and key not in self._hits:
                stale = [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]
                for k in stale:
                    del self._hits[k]

            timestamps = self._hits[key]
            # Prune expired entries
            self._hits[key] = [t for t in timestamps if t > cutoff]
            if len(self._hits[key]) >= self.max_requests:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many requests. Please try again later.",
                )
            self._hits[key].append(now)

    def reset(self) -> None:
        """Clear all tracked hits (for testing)."""
        with self._lock:
            self._hits.clear()


# Pre-auth endpoints: 10 requests per 60 seconds per IP
_preauth_limiter = _SlidingWindow(max_requests=10, window_seconds=60)


def _get_client_ip(request: Request) -> str:
    """Extract the real client IP, using X-Forwarded-For when behind a proxy.

    Cloud Run always sets X-Forwarded-For with the real client IP as the
    first entry. We use it when present to avoid rate-limiting all users
    collectively via the load balancer IP.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # First IP in the chain is the original client
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_rate_limit(request: Request) -> None:
    """FastAPI dependency that enforces rate limiting by client IP."""
    _preauth_limiter.check(_get_client_ip(request))


# EHR navigate: per-user daily rate limit (lazily initialized from settings)
_ehr_navigate_limiter: _SlidingWindow | None = None


def get_ehr_navigate_limiter() -> _SlidingWindow:
    """Get the per-user daily rate limiter for EHR navigate endpoint."""
    global _ehr_navigate_limiter  # noqa: PLW0603
    if _ehr_navigate_limiter is None:
        from .settings import get_settings  # noqa: PLC0415 — lazy to avoid circular import

        settings = get_settings()
        _ehr_navigate_limiter = _SlidingWindow(
            max_requests=settings.ehr_navigate_daily_limit,
            window_seconds=86_400,
        )
    return _ehr_navigate_limiter
