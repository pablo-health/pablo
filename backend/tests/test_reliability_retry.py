# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the retry/backoff engine (``call_with_retry``/``acall_with_retry``).

All hermetic — no network, no real sleeping (``sleep=`` is always
injected). Covers: attempt counting and exhaustion for both the sync
and async entry points, non-retryable passthrough, deadline
enforcement, idempotency-gated retryability (the ``UNSAFE`` pre-
dispatch-only rule), the ``Retry-After`` override, and a thin
integration test driving the engine through a real ``httpx``
``MockTransport``.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import httpx
import pytest
from app.reliability import (
    Idempotency,
    RetryExhaustedError,
    RetryPolicy,
    acall_with_retry,
    call_with_retry,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class _Flaky:
    """Calls that fail N times then succeed, or always fail."""

    def __init__(self, failures: list[BaseException]) -> None:
        self._failures = list(failures)
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self._failures:
            raise self._failures.pop(0)
        return "ok"

    async def acall(self) -> str:
        return self()


def _sleeps(record: list[float]) -> Callable[[float], None]:
    def _sync(delay: float) -> None:
        record.append(delay)

    return _sync


async def _async_sleeps(record: list[float], delay: float) -> None:
    record.append(delay)


# ---------------------------------------------------------------------------
# Sync engine
# ---------------------------------------------------------------------------


class TestCallWithRetrySync:
    def test_fails_twice_then_succeeds(self) -> None:
        flaky = _Flaky([TimeoutError("slow"), TimeoutError("slow again")])
        sleeps: list[float] = []
        result = call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=3, base_delay=1.0, jitter=False),
            idempotency=Idempotency.SAFE,
            sleep=_sleeps(sleeps),
        )
        assert result == "ok"
        assert flaky.calls == 3
        assert sleeps == [1.0, 2.0]

    def test_always_fails_raises_exhausted_with_attempts_and_cause(self) -> None:
        cause = TimeoutError("always slow")
        flaky = _Flaky([TimeoutError("1"), TimeoutError("2"), cause])
        with pytest.raises(RetryExhaustedError) as exc_info:
            call_with_retry(
                flaky,
                policy=RetryPolicy(max_attempts=3, base_delay=0, jitter=False),
                idempotency=Idempotency.SAFE,
                sleep=lambda _d: None,
            )
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_exc, TimeoutError)
        assert flaky.calls == 3

    def test_non_retryable_propagates_on_first_attempt_no_sleep(self) -> None:
        flaky = _Flaky([ValueError("nope")])
        sleeps: list[float] = []
        with pytest.raises(ValueError, match="nope"):
            call_with_retry(
                flaky,
                policy=RetryPolicy(max_attempts=3),
                idempotency=Idempotency.SAFE,
                sleep=_sleeps(sleeps),
            )
        assert flaky.calls == 1
        assert sleeps == []

    def test_deadline_exceeded_stops_early(self) -> None:
        flaky = _Flaky([TimeoutError("1"), TimeoutError("2"), TimeoutError("3")])
        with pytest.raises(RetryExhaustedError) as exc_info:
            call_with_retry(
                flaky,
                policy=RetryPolicy(max_attempts=10, base_delay=100.0, jitter=False, deadline=5.0),
                idempotency=Idempotency.SAFE,
                sleep=lambda _d: None,
            )
        # First attempt fails, next backoff (100s) alone blows the 5s
        # deadline — stop after exactly one retry attempt was made.
        assert exc_info.value.attempts == 1
        assert flaky.calls == 1

    def test_on_retry_callback_receives_attempt_exc_delay(self) -> None:
        flaky = _Flaky([TimeoutError("slow")])
        seen: list[tuple[int, BaseException, float]] = []
        call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=3.0, jitter=False),
            idempotency=Idempotency.SAFE,
            on_retry=lambda attempt, exc, delay: seen.append((attempt, exc, delay)),
            sleep=lambda _d: None,
        )
        assert len(seen) == 1
        attempt, exc, delay = seen[0]
        assert attempt == 1
        assert isinstance(exc, TimeoutError)
        assert delay == pytest.approx(3.0)

    def test_max_delay_caps_backoff(self) -> None:
        flaky = _Flaky([TimeoutError("1"), TimeoutError("2"), TimeoutError("3")])
        sleeps: list[float] = []
        call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=4, base_delay=10.0, max_delay=15.0, jitter=False),
            idempotency=Idempotency.SAFE,
            sleep=_sleeps(sleeps),
        )
        # 10, 20->capped 15, 40->capped 15
        assert sleeps == [10.0, 15.0, 15.0]

    def test_jitter_produces_a_value_within_bounds(self) -> None:
        random.seed(0)
        flaky = _Flaky([TimeoutError("slow")])
        sleeps: list[float] = []
        call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=2.0, jitter=True),
            idempotency=Idempotency.SAFE,
            sleep=_sleeps(sleeps),
        )
        assert len(sleeps) == 1
        assert 0.0 <= sleeps[0] <= 2.0

    def test_custom_retryable_override_replaces_default_classifier(self) -> None:
        # ValueError isn't transient by the default classifier, but a
        # caller-supplied predicate can still opt it in.
        flaky = _Flaky([ValueError("domain-specific retry signal")])
        result = call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=0),
            idempotency=Idempotency.SAFE,
            retryable=lambda exc: isinstance(exc, ValueError),
            sleep=lambda _d: None,
        )
        assert result == "ok"
        assert flaky.calls == 2

    def test_retry_after_header_overrides_backoff(self) -> None:
        request = httpx.Request("GET", "https://example.test")
        response = httpx.Response(429, request=request, headers={"Retry-After": "5"})
        exc = httpx.HTTPStatusError("429", request=request, response=response)
        flaky = _Flaky([exc])
        sleeps: list[float] = []
        call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=1.0, max_delay=8.0, jitter=False),
            idempotency=Idempotency.SAFE,
            sleep=_sleeps(sleeps),
        )
        assert sleeps == [5.0]

    def test_retry_after_header_is_capped_at_max_delay(self) -> None:
        # A misconfigured or malicious Retry-After must not sleep
        # unbounded, especially under a deadline-free preset (LLM_JOB /
        # HTTP_JOB) where nothing else would catch it.
        request = httpx.Request("GET", "https://example.test")
        response = httpx.Response(429, request=request, headers={"Retry-After": "9999"})
        exc = httpx.HTTPStatusError("429", request=request, response=response)
        flaky = _Flaky([exc])
        sleeps: list[float] = []
        call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=1.0, max_delay=8.0, jitter=False),
            idempotency=Idempotency.SAFE,
            sleep=_sleeps(sleeps),
        )
        assert sleeps == [8.0]


# ---------------------------------------------------------------------------
# Async engine
# ---------------------------------------------------------------------------


class TestACallWithRetryAsync:
    # OSS test infrastructure does not ship pytest-asyncio; each test wraps
    # its async body with ``asyncio.run`` (matching ``test_chat_turn_service.py``).

    def test_fails_once_then_succeeds(self) -> None:
        flaky = _Flaky([TimeoutError("slow")])
        sleeps: list[float] = []

        async def _impl() -> str:
            return await acall_with_retry(
                flaky.acall,
                policy=RetryPolicy(max_attempts=2, base_delay=0.25, jitter=False),
                idempotency=Idempotency.SAFE,
                sleep=lambda d: _async_sleeps(sleeps, d),
            )

        result = asyncio.run(_impl())
        assert result == "ok"
        assert flaky.calls == 2
        assert sleeps == [0.25]

    def test_always_fails_raises_exhausted(self) -> None:
        flaky = _Flaky([TimeoutError("1"), TimeoutError("2")])

        async def _impl() -> None:
            await acall_with_retry(
                flaky.acall,
                policy=RetryPolicy(max_attempts=2, base_delay=0),
                idempotency=Idempotency.SAFE,
                sleep=lambda d: _async_sleeps([], d),
            )

        with pytest.raises(RetryExhaustedError) as exc_info:
            asyncio.run(_impl())
        assert exc_info.value.attempts == 2

    def test_non_retryable_propagates_without_sleep(self) -> None:
        flaky = _Flaky([ValueError("nope")])
        sleeps: list[float] = []

        async def _impl() -> None:
            await acall_with_retry(
                flaky.acall,
                policy=RetryPolicy(max_attempts=3),
                idempotency=Idempotency.SAFE,
                sleep=lambda d: _async_sleeps(sleeps, d),
            )

        with pytest.raises(ValueError, match="nope"):
            asyncio.run(_impl())
        assert flaky.calls == 1
        assert sleeps == []

    def test_deadline_wraps_attempt_in_timeout(self) -> None:
        async def _hangs() -> str:
            await asyncio.sleep(10)
            return "unreachable"

        async def _impl() -> None:
            await acall_with_retry(
                _hangs,
                policy=RetryPolicy(max_attempts=2, base_delay=0, deadline=0.05),
                idempotency=Idempotency.SAFE,
                sleep=lambda d: _async_sleeps([], d),
            )

        with pytest.raises(RetryExhaustedError) as exc_info:
            asyncio.run(_impl())
        assert isinstance(exc_info.value.last_exc, TimeoutError)


# ---------------------------------------------------------------------------
# Idempotency-gated retryability
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_unsafe_post_dispatch_read_timeout_not_retried(self) -> None:
        exc = httpx.ReadTimeout("slow read")
        flaky = _Flaky([exc])
        with pytest.raises(httpx.ReadTimeout):
            call_with_retry(
                flaky,
                policy=RetryPolicy(max_attempts=3),
                idempotency=Idempotency.UNSAFE,
                sleep=lambda _d: None,
            )
        assert flaky.calls == 1

    def test_unsafe_connect_error_is_retried(self) -> None:
        flaky = _Flaky([httpx.ConnectError("refused")])
        result = call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=0),
            idempotency=Idempotency.UNSAFE,
            sleep=lambda _d: None,
        )
        assert result == "ok"
        assert flaky.calls == 2

    def test_safe_read_timeout_is_retried(self) -> None:
        flaky = _Flaky([httpx.ReadTimeout("slow read")])
        result = call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=0),
            idempotency=Idempotency.SAFE,
            sleep=lambda _d: None,
        )
        assert result == "ok"
        assert flaky.calls == 2

    def test_keyed_behaves_like_safe(self) -> None:
        flaky = _Flaky([httpx.ReadTimeout("slow read")])
        result = call_with_retry(
            flaky,
            policy=RetryPolicy(max_attempts=2, base_delay=0),
            idempotency=Idempotency.KEYED,
            sleep=lambda _d: None,
        )
        assert result == "ok"
        assert flaky.calls == 2


# ---------------------------------------------------------------------------
# Thin integration: a real httpx MockTransport through the real engine
# ---------------------------------------------------------------------------


class TestHttpxIntegration:
    def test_two_503s_then_200_succeeds_on_third_attempt(self) -> None:
        responses = iter([503, 503, 200])

        def handler(request: httpx.Request) -> httpx.Response:
            status = next(responses)
            return httpx.Response(status, request=request, json={"ok": status == 200})

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:

            def _call() -> httpx.Response:
                response = client.get("https://example.test/v1/resource")
                response.raise_for_status()
                return response

            result = call_with_retry(
                _call,
                policy=RetryPolicy(max_attempts=3, base_delay=0),
                idempotency=Idempotency.SAFE,
                sleep=lambda _d: None,
            )
        assert result.status_code == 200
        assert result.json() == {"ok": True}

    def test_400_raises_without_retry(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(400, request=request)

        transport = httpx.MockTransport(handler)
        with httpx.Client(transport=transport) as client:

            def _call() -> httpx.Response:
                response = client.get("https://example.test/v1/resource")
                response.raise_for_status()
                return response

            with pytest.raises(httpx.HTTPStatusError):
                call_with_retry(
                    _call,
                    policy=RetryPolicy(max_attempts=3, base_delay=0),
                    idempotency=Idempotency.SAFE,
                    sleep=lambda _d: None,
                )
        assert calls == 1
