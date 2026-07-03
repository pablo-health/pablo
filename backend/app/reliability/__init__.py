# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Shared reliability primitives for outbound calls: retry, backoff, classification.

See :mod:`.retry` for the engine and :mod:`.classify` for the
cross-SDK transient-failure classifier the engine's default retryable
check is built from.
"""

from __future__ import annotations

from .classify import is_pre_dispatch, is_transient, retry_after_seconds, status_is_transient
from .retry import (
    HTTP_JOB,
    HTTP_REQUEST,
    LLM_JOB,
    LLM_REQUEST,
    Idempotency,
    RetryExhaustedError,
    RetryPolicy,
    acall_with_retry,
    call_with_retry,
)

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
    "is_pre_dispatch",
    "is_transient",
    "retry_after_seconds",
    "status_is_transient",
]
