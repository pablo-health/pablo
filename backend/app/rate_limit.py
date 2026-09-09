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
import uuid
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

    def __init__(self, max_requests: int, window_seconds: int, *, max_keys: int = 10_000) -> None:
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

        member = f"{uuid.uuid4().hex}:{now}"

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(rkey, 0, cutoff)
        pipe.zcard(rkey)
        pipe.zadd(rkey, {member: now})
        pipe.expire(rkey, self.window_seconds + 1)
        results = pipe.execute()

        count = results[1]  # zcard result (before adding new entry)
        if count >= self.max_requests:
            self._redis.zrem(rkey, member)
            raise _TOO_MANY_REQUESTS

    def reset(self) -> None:
        # Scan and delete all rate limit keys
        for rkey in self._redis.scan_iter(f"{self.KEY_PREFIX}*"):
            self._redis.delete(rkey)


class NamespacedLimiter:
    """Wraps a limiter, prefixing every key before it reaches the store.

    Both ``RedisSlidingWindow`` and ``InMemorySlidingWindow`` key their
    storage purely off the string passed to ``check``. If two independent
    limiters (e.g. chat-send and audio-upload) are ever handed the same raw
    key — a bare user id — they silently share one budget. Namespacing at
    the limiter itself, rather than at each call site, means a new limiter
    can't reintroduce that collision just by forgetting to prefix its key.
    """

    def __init__(self, limiter: RateLimiter, namespace: str) -> None:
        self._limiter = limiter
        self._namespace = namespace

    def check(self, key: str) -> None:
        self._limiter.check(f"{self._namespace}{key}")

    def reset(self) -> None:
        self._limiter.reset()


class CompositeLimiter:
    """Enforces several sliding windows at once (e.g. per-minute + per-hour).

    ``check`` runs each window in turn; the first one to breach raises
    ``429`` and short-circuits. This lets a single logical limit combine a
    tight burst window with a looser sustained-rate window so a caller can
    neither spike nor grind against an endpoint.

    Each sub-window is wrapped in its own :class:`NamespacedLimiter` suffix
    so the windows never share a key: on the Redis path, a shared key would
    mean the per-minute window's pruning wipes entries before the per-hour
    window ever sees them, making the per-hour limit unreachable.
    """

    def __init__(self, limiters: list[RateLimiter]) -> None:
        self._limiters = limiters

    def check(self, key: str) -> None:
        for limiter in self._limiters:
            limiter.check(key)

    def reset(self) -> None:
        for limiter in self._limiters:
            limiter.reset()


def _create_limiter(max_requests: int, window_seconds: int) -> RateLimiter:
    """Create the appropriate limiter based on settings."""
    from .redis_client import get_redis_client  # noqa: PLC0415

    client = get_redis_client()
    if client is not None:
        return RedisSlidingWindow(max_requests, window_seconds, client)
    return InMemorySlidingWindow(max_requests, window_seconds)


def _create_windowed_limiter(namespace: str, max_requests: int, window_seconds: int) -> RateLimiter:
    """Create a limiter namespaced by endpoint and window length.

    The window suffix (e.g. ``:60s``) is what keeps a :class:`CompositeLimiter`'s
    sub-windows from colliding with each other; the namespace prefix is what
    keeps different endpoints' limiters from colliding with each other.
    """
    limiter = _create_limiter(max_requests, window_seconds)
    return NamespacedLimiter(limiter, f"{namespace}:{window_seconds}s:")


# Pre-auth endpoints: 10 requests per 60 seconds per IP
_preauth_limiter: RateLimiter | None = None


def _get_preauth_limiter() -> RateLimiter:
    global _preauth_limiter  # noqa: PLW0603
    if _preauth_limiter is None:
        _preauth_limiter = _create_limiter(max_requests=10, window_seconds=60)
        logger.info("Pre-auth rate limiter: %s", type(_preauth_limiter).__name__)
    return _preauth_limiter


# Passkey sign-in and recovery: separate budgets, for the same reason public
# booking has its own (see below). Both used to draw on the pre-auth window
# above, and sharing was wrong twice over.
#
# The limit keys on client IP, so a practice behind one office address is a
# single key, and one passkey sign-in costs TWO requests (authenticate/begin
# then authenticate/finish). Ten per minute is therefore five sign-ins per
# minute for the whole practice — a morning arrival exhausts it, and the
# failures land on sign-in and recovery and the native app's code/exchange
# endpoints alike, because they all share the window.
#
# Worse, recovery-code redemption is what someone reaches for when their
# passkey is not working. Drawing on the same budget as the sign-in attempts
# they just made makes the failure self-reinforcing: sign-in struggles, the
# user retries, the retries spend the window, and the recovery path is refused
# at exactly the moment it is needed.
#
# Sizes: sign-in is loose because the traffic is legitimate and bursty.
# Redemption stays tight — it is a rare, one-attempt event (normalize_code
# already forgives case, spaces and dashes, so a typo rarely costs a retry),
# and until per-account limiting landed alongside this, the IP window was the
# only brute-force control it had.
_passkey_login_limiter: RateLimiter | None = None
_recovery_redeem_limiter: RateLimiter | None = None
_recovery_redeem_account_limiter: RateLimiter | None = None


def _get_passkey_login_limiter() -> RateLimiter:
    global _passkey_login_limiter  # noqa: PLW0603
    if _passkey_login_limiter is None:
        _passkey_login_limiter = _create_limiter(max_requests=60, window_seconds=60)
        logger.info("Passkey login rate limiter: %s", type(_passkey_login_limiter).__name__)
    return _passkey_login_limiter


def _get_recovery_redeem_limiter() -> RateLimiter:
    global _recovery_redeem_limiter  # noqa: PLW0603
    if _recovery_redeem_limiter is None:
        _recovery_redeem_limiter = _create_limiter(max_requests=10, window_seconds=60)
        logger.info("Recovery redeem rate limiter: %s", type(_recovery_redeem_limiter).__name__)
    return _recovery_redeem_limiter


def _get_recovery_redeem_account_limiter() -> RateLimiter:
    global _recovery_redeem_account_limiter  # noqa: PLW0603
    if _recovery_redeem_account_limiter is None:
        _recovery_redeem_account_limiter = _create_limiter(max_requests=5, window_seconds=3_600)
        logger.info(
            "Recovery redeem per-account rate limiter: %s",
            type(_recovery_redeem_account_limiter).__name__,
        )
    return _recovery_redeem_account_limiter


def require_passkey_login_rate_limit(request: Request) -> None:
    """Burst limit for the passkey sign-in ceremony, by client IP.

    Sized for a whole practice signing in at once rather than a single person,
    since a shared office address is one key here.
    """
    _get_passkey_login_limiter().check(f"passkey-login:{get_client_ip(request)}")


def require_recovery_redeem_rate_limit(request: Request) -> None:
    """Limit on recovery-code redemption, by client IP.

    Deliberately not shared with sign-in: the whole point is that this path
    still works when sign-in is failing.
    """
    _get_recovery_redeem_limiter().check(f"recovery-redeem:{get_client_ip(request)}")


def check_recovery_redeem_account_limit(user_id: str) -> None:
    """Limit recovery-code redemption per ACCOUNT, independent of address.

    The IP window above protects the service; it does not protect an account.
    An attacker rotates addresses and slips under it, while a legitimate user
    is punished for traffic their colleagues generated. Keying on the account
    is what actually bounds guessing against one person's codes, and it is the
    control the IP budget above is allowed to stay generous because of.

    Redemption runs on an already-authenticated first-factor session, so the
    account is known here and there is no anonymous caller to key on.
    """
    _get_recovery_redeem_account_limiter().check(f"recovery-redeem-account:{user_id}")


def get_client_ip(request: Request) -> str:
    """Extract the real client IP from X-Forwarded-For when behind a proxy.

    The client controls the *leftmost* X-Forwarded-For entries (it can send
    any value it likes), while a trusted reverse proxy *appends* the real
    peer IP on the right. Reading the leftmost entry therefore lets a caller
    forge a unique IP per request and defeat the rate limiter entirely. We
    instead read ``settings.trusted_proxy_hops`` entries from the right —
    the proxy-controlled end — which is the real client IP for our Cloud Run
    deployment (one trusted hop) and stays correct if more proxies are added.
    """
    from .settings import get_settings  # noqa: PLC0415

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            hops = min(get_settings().trusted_proxy_hops, len(parts))
            return parts[-hops]
    return request.client.host if request.client else "unknown"


def require_rate_limit(request: Request) -> None:
    """FastAPI dependency that enforces rate limiting by client IP."""
    _get_preauth_limiter().check(get_client_ip(request))


# Public booking (docs/design/public-booking.md): the anonymous booking
# surface gets its own budget instead of sharing the pre-auth window with
# login and signup. Sharing was wrong in both directions — browsing two
# weeks of availability is ~15 requests and would exhaust a 10/60s login
# budget, and booking traffic from one NAT'd address would lock out
# logins for everyone behind it.
#
# Two windows with *distinct key namespaces*: a loose browse window that
# a real visitor clicking through dates will not hit, and a tight
# sustained window on the write, which is what actually costs something
# (a patient record and an appointment). The namespaces matter — the
# Redis limiter keys purely on the string it is handed, so two limiters
# passed the same key silently share one window.
_public_booking_browse_limiter: RateLimiter | None = None
_public_booking_write_limiter: RateLimiter | None = None


def _get_public_booking_browse_limiter() -> RateLimiter:
    global _public_booking_browse_limiter  # noqa: PLW0603
    if _public_booking_browse_limiter is None:
        _public_booking_browse_limiter = _create_limiter(max_requests=60, window_seconds=60)
        logger.info(
            "Public booking browse rate limiter: %s",
            type(_public_booking_browse_limiter).__name__,
        )
    return _public_booking_browse_limiter


def _get_public_booking_write_limiter() -> RateLimiter:
    global _public_booking_write_limiter  # noqa: PLW0603
    if _public_booking_write_limiter is None:
        _public_booking_write_limiter = _create_limiter(max_requests=10, window_seconds=3_600)
        logger.info(
            "Public booking write rate limiter: %s",
            type(_public_booking_write_limiter).__name__,
        )
    return _public_booking_write_limiter


def require_public_booking_rate_limit(request: Request) -> None:
    """Browse-surface limit for the public booking endpoints, by client IP."""
    _get_public_booking_browse_limiter().check(f"public-booking:{get_client_ip(request)}")


def require_public_booking_write_rate_limit(request: Request) -> None:
    """Sustained limit on the booking POST, by client IP.

    Bounds how many patient records and appointments one address can
    create through a link. Applied on top of the browse limit, which
    still provides the burst window.
    """
    _get_public_booking_write_limiter().check(f"public-booking-write:{get_client_ip(request)}")


# EHR navigate: per-user daily rate limit (lazily initialized from settings)
_ehr_navigate_limiter: RateLimiter | None = None


def get_ehr_navigate_limiter() -> RateLimiter:
    """Get the per-user daily rate limiter for EHR navigate endpoint."""
    global _ehr_navigate_limiter  # noqa: PLW0603
    if _ehr_navigate_limiter is None:
        from .settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        _ehr_navigate_limiter = _create_windowed_limiter(
            "ehr-nav",
            max_requests=settings.ehr_navigate_daily_limit,
            window_seconds=86_400,
        )
        logger.info("EHR navigate rate limiter: %s", type(_ehr_navigate_limiter).__name__)
    return _ehr_navigate_limiter


# Availability parse: per-user daily rate limit (lazily initialized from settings).
_availability_parse_limiter: RateLimiter | None = None


def get_availability_parse_limiter() -> RateLimiter:
    """Get the per-user daily rate limiter for the availability-rule parse endpoint."""
    global _availability_parse_limiter  # noqa: PLW0603
    if _availability_parse_limiter is None:
        from .settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        _availability_parse_limiter = _create_limiter(
            max_requests=settings.availability_parse_daily_limit,
            window_seconds=86_400,
        )
        logger.info(
            "Availability parse rate limiter: %s", type(_availability_parse_limiter).__name__
        )
    return _availability_parse_limiter


# Chat send: per-user burst limit (per-minute + per-hour sliding windows).
_chat_send_limiter: RateLimiter | None = None


def get_chat_send_limiter() -> RateLimiter:
    """Get the per-user burst rate limiter for the chat-send endpoint.

    Combines a per-minute and a per-hour window so a single authenticated
    caller cannot drive unbounded LLM spend by hammering the endpoint. This
    is per-deployment abuse protection, not a usage quota.
    """
    global _chat_send_limiter  # noqa: PLW0603
    if _chat_send_limiter is None:
        from .settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        _chat_send_limiter = CompositeLimiter(
            [
                _create_windowed_limiter(
                    "chat-send", max_requests=settings.chat_rate_per_min, window_seconds=60
                ),
                _create_windowed_limiter(
                    "chat-send", max_requests=settings.chat_rate_per_hour, window_seconds=3_600
                ),
            ]
        )
        logger.info("Chat send rate limiter: %s", type(_chat_send_limiter).__name__)
    return _chat_send_limiter


# Audio upload: per-user burst limit (per-minute + per-hour sliding windows).
_audio_upload_limiter: RateLimiter | None = None


def get_audio_upload_limiter() -> RateLimiter:
    """Get the per-user burst rate limiter for the audio-upload endpoint.

    Combines a per-minute and a per-hour window so a single authenticated
    caller cannot drive unbounded transcription spend by hammering the
    endpoint. This is per-deployment abuse protection, not a usage quota.
    """
    global _audio_upload_limiter  # noqa: PLW0603
    if _audio_upload_limiter is None:
        from .settings import get_settings  # noqa: PLC0415

        settings = get_settings()
        _audio_upload_limiter = CompositeLimiter(
            [
                _create_windowed_limiter(
                    "audio-upload", max_requests=settings.upload_rate_per_min, window_seconds=60
                ),
                _create_windowed_limiter(
                    "audio-upload", max_requests=settings.upload_rate_per_hour, window_seconds=3_600
                ),
            ]
        )
        logger.info("Audio upload rate limiter: %s", type(_audio_upload_limiter).__name__)
    return _audio_upload_limiter


def reset_preauth_limiter() -> None:
    """Reset the pre-auth rate limiter. Used by tests."""
    _get_preauth_limiter().reset()


def reset_public_booking_limiters() -> None:
    """Reset both public-booking rate limiters. Used by tests."""
    _get_public_booking_browse_limiter().reset()
    _get_public_booking_write_limiter().reset()


def reset_passkey_limiters() -> None:
    """Reset the passkey sign-in and recovery limiters. Used by tests.

    All three, including the per-account one — a test that redeems a code
    several times would otherwise carry that budget into the next test.
    """
    _get_passkey_login_limiter().reset()
    _get_recovery_redeem_limiter().reset()
    _get_recovery_redeem_account_limiter().reset()
