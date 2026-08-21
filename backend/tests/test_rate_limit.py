# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the sliding-window rate limiters.

Covers the per-user burst guard used by the chat-send and audio-upload
endpoints: a :class:`CompositeLimiter` stacks a tight per-minute window on
top of a looser per-hour window so a single caller can neither spike nor
grind against an expensive endpoint. The first window to breach raises 429.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.rate_limit import (
    CompositeLimiter,
    InMemorySlidingWindow,
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
