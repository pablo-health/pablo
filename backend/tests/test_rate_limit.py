# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the sliding-window rate limiters.

Covers the per-user burst guard used by the chat-send and audio-upload
endpoints: a :class:`CompositeLimiter` stacks a tight per-minute window on
top of a looser per-hour window so a single caller can neither spike nor
grind against an expensive endpoint. The first window to breach raises 429.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app import rate_limit
from app.rate_limit import (
    CompositeLimiter,
    InMemorySlidingWindow,
    NamespacedLimiter,
    RedisSlidingWindow,
    _get_preauth_limiter,
    _get_public_booking_browse_limiter,
    _get_public_booking_write_limiter,
    require_public_booking_rate_limit,
    require_public_booking_write_rate_limit,
    reset_preauth_limiter,
    reset_public_booking_limiters,
)
from fastapi import HTTPException, status


def test_in_memory_window_allows_up_to_limit_then_429() -> None:
    limiter = InMemorySlidingWindow(max_requests=3, window_seconds=60)

    for _ in range(3):
        limiter.check("user-1")

    with pytest.raises(HTTPException) as exc:
        limiter.check("user-1")
    assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_in_memory_window_isolates_keys() -> None:
    limiter = InMemorySlidingWindow(max_requests=1, window_seconds=60)

    limiter.check("user-1")
    # A different key has its own budget and is unaffected.
    limiter.check("user-2")

    with pytest.raises(HTTPException):
        limiter.check("user-1")


def test_composite_enforces_tightest_window_first() -> None:
    # 2 per minute on top of 100 per hour: the per-minute window trips first.
    per_min = InMemorySlidingWindow(max_requests=2, window_seconds=60)
    per_hour = InMemorySlidingWindow(max_requests=100, window_seconds=3_600)
    limiter = CompositeLimiter([per_min, per_hour])

    limiter.check("user-1")
    limiter.check("user-1")

    with pytest.raises(HTTPException) as exc:
        limiter.check("user-1")
    assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_composite_enforces_looser_window_over_a_longer_horizon() -> None:
    # A generous per-minute window but a tight per-hour cap: the hourly
    # window is what eventually stops a sustained caller.
    per_min = InMemorySlidingWindow(max_requests=100, window_seconds=60)
    per_hour = InMemorySlidingWindow(max_requests=3, window_seconds=3_600)
    limiter = CompositeLimiter([per_min, per_hour])

    for _ in range(3):
        limiter.check("user-1")

    with pytest.raises(HTTPException) as exc:
        limiter.check("user-1")
    assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS


def test_composite_reset_clears_all_windows() -> None:
    per_min = InMemorySlidingWindow(max_requests=1, window_seconds=60)
    per_hour = InMemorySlidingWindow(max_requests=1, window_seconds=3_600)
    limiter = CompositeLimiter([per_min, per_hour])

    limiter.check("user-1")
    with pytest.raises(HTTPException):
        limiter.check("user-1")

    limiter.reset()
    # Both windows are clear, so the caller has a fresh budget.
    limiter.check("user-1")


# ------------------------------------------------------- public booking links


class _FakeRequest:
    """Minimal Request stand-in: the limiters only read client host/headers."""

    def __init__(self, ip: str) -> None:
        self.headers: dict[str, str] = {}
        self.client = type("Client", (), {"host": ip})()


@pytest.fixture(autouse=True)
def _clean_limiters() -> Any:
    reset_preauth_limiter()
    reset_public_booking_limiters()
    yield
    reset_preauth_limiter()
    reset_public_booking_limiters()


def test_public_booking_does_not_share_the_preauth_window() -> None:
    """Browsing a booking page must not consume anyone's login budget.

    The two limiters run over the same client IP, so separation comes
    from the key namespace, not from holding distinct objects — a Redis
    limiter keys purely on the string it is handed.
    """
    request = _FakeRequest("203.0.113.7")
    for _ in range(20):
        require_public_booking_rate_limit(request)

    # The pre-auth window (10/60s) is untouched: login still works from
    # the same address.
    for _ in range(10):
        _get_preauth_limiter().check("203.0.113.7")


def test_public_booking_browse_window_covers_a_full_page_visit() -> None:
    """Card + a slots call per date shown must not trip the limit."""
    request = _FakeRequest("203.0.113.8")
    # 1 card + 14 dates (DAYS_SHOWN in the booking page) + 1 booking POST,
    # with room to spare for a revisit.
    for _ in range(16):
        require_public_booking_rate_limit(request)


def test_public_booking_write_window_is_tighter_than_browsing() -> None:
    """Creating charts is bounded well below the browse budget."""
    request = _FakeRequest("203.0.113.9")
    for _ in range(10):
        require_public_booking_write_rate_limit(request)

    with pytest.raises(HTTPException) as exc:
        require_public_booking_write_rate_limit(request)
    assert exc.value.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    # Browsing still works — a booker who hit the write cap can still
    # read the page.
    require_public_booking_rate_limit(request)


def test_public_booking_windows_isolate_by_client_ip() -> None:
    for _ in range(10):
        require_public_booking_write_rate_limit(_FakeRequest("198.51.100.1"))
    require_public_booking_write_rate_limit(_FakeRequest("198.51.100.2"))


def test_public_booking_limiters_are_distinct_objects() -> None:
    assert _get_public_booking_browse_limiter() is not _get_public_booking_write_limiter()
    assert _get_public_booking_browse_limiter() is not _get_preauth_limiter()


# ------------------------------------------------------------- redis limiters


class _FakePipeline:
    """Replays queued ops against a dict-of-sorted-sets store on execute()."""

    def __init__(self, store: dict[str, dict[str, float]]) -> None:
        self._store = store
        self._ops: list[tuple] = []

    def zremrangebyscore(self, key: str, min_score: float, max_score: float) -> _FakePipeline:
        self._ops.append(("zremrangebyscore", key, min_score, max_score))
        return self

    def zcard(self, key: str) -> _FakePipeline:
        self._ops.append(("zcard", key))
        return self

    def zadd(self, key: str, mapping: dict[str, float]) -> _FakePipeline:
        self._ops.append(("zadd", key, mapping))
        return self

    def expire(self, key: str, seconds: int) -> _FakePipeline:
        self._ops.append(("expire", key, seconds))
        return self

    def execute(self) -> list:
        results = []
        for op in self._ops:
            match op:
                case ("zremrangebyscore", key, min_score, max_score):
                    zset = self._store.setdefault(key, {})
                    stale = [m for m, s in zset.items() if min_score <= s <= max_score]
                    for member in stale:
                        del zset[member]
                    results.append(len(stale))
                case ("zcard", key):
                    results.append(len(self._store.get(key, {})))
                case ("zadd", key, mapping):
                    zset = self._store.setdefault(key, {})
                    zset.update(mapping)
                    results.append(len(mapping))
                case ("expire", _key, _seconds):
                    results.append(True)
        self._ops = []
        return results


class _FakeRedis:
    """Minimal sorted-set Redis stand-in covering what RedisSlidingWindow uses."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, float]] = {}

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self.store)

    def zrem(self, key: str, member: str) -> None:
        self.store.get(key, {}).pop(member, None)

    def scan_iter(self, pattern: str):
        prefix = pattern.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


class _FakeClock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def time(self) -> float:
        return self.now


def _settings_stub(**overrides: int) -> MagicMock:
    settings = MagicMock()
    settings.chat_rate_per_min = overrides.get("chat_rate_per_min", 100)
    settings.chat_rate_per_hour = overrides.get("chat_rate_per_hour", 100)
    settings.upload_rate_per_min = overrides.get("upload_rate_per_min", 100)
    settings.upload_rate_per_hour = overrides.get("upload_rate_per_hour", 100)
    settings.ehr_navigate_daily_limit = overrides.get("ehr_navigate_daily_limit", 100)
    return settings


@pytest.fixture(autouse=True)
def _reset_limiter_singletons():
    """The get_*_limiter() factories cache a module-level singleton."""
    rate_limit._chat_send_limiter = None
    rate_limit._audio_upload_limiter = None
    rate_limit._ehr_navigate_limiter = None
    yield
    rate_limit._chat_send_limiter = None
    rate_limit._audio_upload_limiter = None
    rate_limit._ehr_navigate_limiter = None


def test_redis_composite_hourly_window_survives_per_minute_pruning() -> None:
    """A per-hour window must keep counting even though the per-minute
    window in the same CompositeLimiter prunes its own entries every check.

    Before key isolation, both sub-windows wrote to the same Redis sorted
    set, so the per-minute window's zremrangebyscore(now - 60, ...) wiped
    every entry each time a request landed more than 60s after the last —
    the per-hour window's zcard never saw more than one stale entry and
    could never reach its own (much higher) threshold.
    """
    clock = _FakeClock()
    fake_redis = _FakeRedis()
    settings = _settings_stub(chat_rate_per_min=100, chat_rate_per_hour=3)

    with (
        patch("app.redis_client.get_redis_client", return_value=fake_redis),
        patch("app.settings.get_settings", return_value=settings),
        patch("app.rate_limit.time.time", clock.time),
    ):
        limiter = rate_limit.get_chat_send_limiter()

        # Four requests spaced 61s apart: each gap outlives the 60s window,
        # so a shared key would get fully pruned before every check.
        raised_at = None
        for i in range(4):
            clock.now = i * 61
            try:
                limiter.check("user-1")
            except HTTPException:
                raised_at = i
                break

        assert raised_at == 3, "expected the per-hour cap (3) to trip on the 4th request"


def test_chat_audio_ehr_limiter_budgets_are_independent() -> None:
    """Chat send, audio upload, and EHR navigate share nothing: exhausting
    one caller's budget on one endpoint must not affect the others, even
    though all three key off the same bare user id at the call site.
    """
    fake_redis = _FakeRedis()
    settings = _settings_stub(
        chat_rate_per_min=1, upload_rate_per_min=1, ehr_navigate_daily_limit=1
    )

    with (
        patch("app.redis_client.get_redis_client", return_value=fake_redis),
        patch("app.settings.get_settings", return_value=settings),
    ):
        chat_limiter = rate_limit.get_chat_send_limiter()
        audio_limiter = rate_limit.get_audio_upload_limiter()
        ehr_limiter = rate_limit.get_ehr_navigate_limiter()

        chat_limiter.check("user-1")
        with pytest.raises(HTTPException):
            chat_limiter.check("user-1")

        # Same raw key, different endpoint limiters: still fresh budgets.
        audio_limiter.check("user-1")
        ehr_limiter.check("user-1")


def test_namespaced_limiter_prefixes_key_before_delegating() -> None:
    underlying = InMemorySlidingWindow(max_requests=1, window_seconds=60)
    a = NamespacedLimiter(underlying, "endpoint-a:")
    b = NamespacedLimiter(underlying, "endpoint-b:")

    a.check("user-1")
    with pytest.raises(HTTPException):
        a.check("user-1")

    # Different namespace, same underlying limiter and raw key: unaffected.
    b.check("user-1")


def test_redis_sliding_window_isolates_differently_prefixed_keys() -> None:
    fake_redis = _FakeRedis()
    per_min = NamespacedLimiter(RedisSlidingWindow(1, 60, fake_redis), "chat-send:60s:")
    per_hour = NamespacedLimiter(RedisSlidingWindow(1, 3600, fake_redis), "chat-send:3600s:")

    per_min.check("user-1")
    per_hour.check("user-1")  # Distinct suffix: its own budget, no collision.

    with pytest.raises(HTTPException):
        per_min.check("user-1")
