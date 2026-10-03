# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Bounded retry/backoff engine for outbound calls.

Adopting this at a call site is a two-part change: (a) plumb the SDK's
own per-attempt deadline (genai ``HttpOptions``, httpx ``timeout=``,
gax ``timeout=``) — this module does not and cannot enforce a timeout
on a call it doesn't control the transport for; (b) wrap the call in
``call_with_retry``/``acall_with_retry`` for attempt count, backoff,
and (for async callers) an optional overall wall-clock budget.

A thunk, not a decorator: callers bind their own arguments, so the
engine stays SDK-agnostic across sync google-genai calls, gRPC (gax),
sync and async httpx, and streaming setup — no per-SDK adapter needed.
Idempotency is a required argument, not a default, because it's a
property of *how* a call is made (does the server dedupe it, can a
retry double-charge a side effect), not something a generic engine can
infer from the exception alone.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from .classify import is_pre_dispatch, is_transient, retry_after_seconds, status_code

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Idempotency(Enum):
    """How safe is this call to re-issue after a failure?"""

    SAFE = "safe"
    """Side-effect-free (reads, LLM generate): retry any transient failure."""

    KEYED = "keyed"
    """Side-effecting but server-deduped by a key: retry like SAFE.

    The caller is asserting a dedup key exists server-side (e.g. an
    idempotency key on a payment API) — this engine does not inject
    one itself.
    """

    UNSAFE = "unsafe"
    """Side-effecting, no dedup: retry ONLY pre-dispatch failures.

    A read-timeout or 5xx that arrives *after* the request bytes were
    sent is not retried; it surfaces raw, since the server may have
    already acted on it.
    """


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    """Total tries, not extra retries — 3 means 1 initial + 2 retries."""

    base_delay: float = 0.5
    """Seconds of backoff before the first retry."""

    max_delay: float = 8.0
    """Per-attempt backoff ceiling, before jitter."""

    deadline: float | None = None
    """Optional wall-clock budget across ALL attempts, in seconds.

    Sync callers are bounded only by the SDK's own socket timeout —
    a blocking call can't be interrupted from outside. Async callers
    additionally wrap each attempt in ``asyncio.timeout`` when this is
    set, so a hung attempt is cancelled once the remaining budget is
    spent.
    """

    attempt_timeout: float | None = None
    """Per-attempt timeout, in seconds, that the call site hands to its SDK.

    With a ``deadline`` the policy must hold
    ``deadline >= max_attempts * attempt_timeout + (max_attempts - 1) * max_delay``,
    so a slow first failure still leaves room for a full second attempt.
    A retry that would not fit is not started.
    """

    jitter: bool = True
    """Full jitter (``random.uniform(0, delay)``) on the backoff."""

    retry_status: frozenset[int] = frozenset({429, 500, 502, 503, 504})
    """HTTP-ish status codes the default classifier treats as transient."""


class RetryExhaustedError(Exception):
    """Every attempt failed on a retryable error.

    Non-retryable errors (4xx, auth, ``ValueError``, …) are never
    wrapped — they propagate on their first occurrence unchanged.
    """

    def __init__(self, *, attempts: int, last_exc: BaseException) -> None:
        super().__init__(f"retry exhausted after {attempts} attempt(s): {last_exc}")
        self.attempts = attempts
        self.last_exc = last_exc


_RATE_LIMITED = 429


def _is_retryable(
    exc: BaseException,
    *,
    idempotency: Idempotency,
    policy: RetryPolicy,
    retryable: Callable[[BaseException], bool] | None,
) -> bool:
    classified = (
        retryable(exc)
        if retryable is not None
        else is_transient(exc, retry_status=policy.retry_status)
    )
    if not classified:
        return False
    if idempotency is Idempotency.UNSAFE:
        return is_pre_dispatch(exc)
    return True


def _backoff_seconds(attempt: int, exc: BaseException, policy: RetryPolicy) -> float:
    """Compute the sleep before the next attempt, honoring Retry-After.

    A 429 with no hint waits at least ``base_delay``: a rate limit has not
    cleared a fraction of a second later.

    The hint is capped at ``policy.max_delay`` — for deadline-free
    presets (``LLM_JOB``/``HTTP_JOB``) an absurd or misconfigured
    ``Retry-After`` would otherwise sleep unbounded.
    """
    hinted = retry_after_seconds(exc)
    if hinted is not None:
        return max(0.0, min(hinted, policy.max_delay))
    delay: float = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    if policy.jitter:
        delay = random.uniform(0, delay)  # noqa: S311 — backoff jitter, not cryptographic use
    if status_code(exc) == _RATE_LIMITED:
        delay = max(delay, min(policy.base_delay, policy.max_delay))
    return delay


def _next_attempt_fits(elapsed: float, delay: float, policy: RetryPolicy) -> bool:
    """Can another attempt, after ``delay``, finish inside the deadline?"""
    if policy.deadline is None:
        return True
    needed = policy.attempt_timeout or 0.0
    return elapsed + delay + needed <= policy.deadline


def call_with_retry[T](
    fn: Callable[[], T],
    *,
    policy: RetryPolicy,
    idempotency: Idempotency,
    retryable: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Call ``fn`` with bounded retry. Sync — bounded only by the SDK's own timeout."""
    start = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:  # classified by _is_retryable below, not swallowed
            if not _is_retryable(exc, idempotency=idempotency, policy=policy, retryable=retryable):
                raise
            if attempt >= policy.max_attempts:
                raise RetryExhaustedError(attempts=attempt, last_exc=exc) from exc
            delay = _backoff_seconds(attempt, exc, policy)
            if not _next_attempt_fits(time.monotonic() - start, delay, policy):
                raise RetryExhaustedError(attempts=attempt, last_exc=exc) from exc
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            if delay > 0:
                sleep(delay)


async def acall_with_retry[T](
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy,
    idempotency: Idempotency,
    retryable: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException, float], None] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Call ``fn`` with bounded retry. Async — wraps each attempt in
    ``asyncio.timeout`` when ``policy.deadline`` is set, since an async
    call *can* be cancelled from outside unlike a blocking sync one."""
    start = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            if policy.deadline is not None:
                remaining = policy.deadline - (time.monotonic() - start)
                if remaining <= 0:
                    raise TimeoutError("retry deadline exceeded before attempt started")
                async with asyncio.timeout(remaining):
                    return await fn()
            return await fn()
        except Exception as exc:  # classified by _is_retryable below, not swallowed
            if not _is_retryable(exc, idempotency=idempotency, policy=policy, retryable=retryable):
                raise
            if attempt >= policy.max_attempts:
                raise RetryExhaustedError(attempts=attempt, last_exc=exc) from exc
            delay = _backoff_seconds(attempt, exc, policy)
            if not _next_attempt_fits(time.monotonic() - start, delay, policy):
                raise RetryExhaustedError(attempts=attempt, last_exc=exc) from exc
            if on_retry is not None:
                on_retry(attempt, exc, delay)
            if delay > 0:
                await sleep(delay)


# ---------------------------------------------------------------------------
# Preset policies
# ---------------------------------------------------------------------------

LLM_REQUEST = RetryPolicy(
    max_attempts=2, base_delay=2.0, max_delay=8.0, deadline=125.0, attempt_timeout=55.0
)
"""One retry inside a 125 s budget — the request path of an interactive call.

Each attempt is bounded at 55 s, so a provider that answers 504 near that
bound still leaves room for a second full attempt."""

LLM_JOB = RetryPolicy(
    max_attempts=4, base_delay=1.0, max_delay=30.0, deadline=None, attempt_timeout=180.0
)
"""More attempts, no overall deadline — batch/cron work bounded by the job timeout."""

HTTP_REQUEST = RetryPolicy(max_attempts=2, base_delay=0.3, max_delay=3.0, deadline=20.0)
"""Request-path HTTP calls other than LLM generation."""

HTTP_JOB = RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=30.0, deadline=None)
"""Batch/cron HTTP calls."""


__all__ = [
    "HTTP_JOB",
    "HTTP_REQUEST",
    "LLM_JOB",
    "LLM_REQUEST",
    "Idempotency",
    "RetryExhaustedError",
    "RetryPolicy",
    "acall_with_retry",
    "call_with_retry",
]
