# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the sliding-window rate limiters.

Covers the per-user burst guard used by the chat-send and audio-upload
endpoints: a :class:`CompositeLimiter` stacks a tight per-minute window on
top of a looser per-hour window so a single caller can neither spike nor
grind against an expensive endpoint. The first window to breach raises 429.
"""

from __future__ import annotations

import pytest
from app.rate_limit import CompositeLimiter, InMemorySlidingWindow
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
