# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Cross-SDK transient-failure classification.

Every outbound call site in the app ends up talking through one of a
handful of transports — httpx (direct or wrapped by the anthropic SDK),
gRPC/REST via ``google.api_core`` (Document AI, and anything gax-based),
or a plain stdlib socket error. This module maps each of those shapes
onto three questions a retry engine needs answered:

* ``is_transient`` — is this worth retrying at all (rate limit, 5xx,
  timeout), as opposed to a client error that will fail identically on
  a second try?
* ``is_pre_dispatch`` — did the failure happen before the request body
  reached the server (connect, DNS, pool-wait), or after? This is the
  line an ``Idempotency.UNSAFE`` call cares about: a retry is safe when
  nothing was sent, not when the server might have received and acted
  on the request even though the response never came back.
* ``retry_after_seconds`` — did the server tell us how long to wait?

Every SDK import here is lazy and guarded by ``ImportError`` so a
process that never touches, say, ``anthropic`` doesn't pay for it and
doesn't need the dependency installed.
"""

from __future__ import annotations

from typing import Any


def status_is_transient(status: int, retry_status: frozenset[int]) -> bool:
    """True when an HTTP-ish status code is in the retryable set."""
    return status in retry_status


def _status_code(exc: BaseException) -> int | None:
    """Pull an HTTP-ish status code off ``exc``, whatever SDK raised it.

    Covers ``httpx.HTTPStatusError`` (``.response.status_code``), the
    anthropic SDK's ``APIStatusError`` family (``.status_code``), and
    ``google.api_core``'s ``GoogleAPICallError`` family (``.code`` — an
    HTTP status int on the exception class itself, e.g.
    ``ServiceUnavailable.code == 503``).
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response = getattr(exc, "response", None)
    response_status = getattr(response, "status_code", None)
    if isinstance(response_status, int):
        return response_status
    return None


def _httpx_transient(exc: BaseException) -> bool:
    try:
        import httpx
    except ImportError:
        return False
    if isinstance(
        exc,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
        ),
    ):
        return True
    return isinstance(exc, httpx.HTTPStatusError)


def _httpx_pre_dispatch(exc: BaseException) -> bool:
    """Connect-phase-only httpx failures: nothing was sent to the server."""
    try:
        import httpx
    except ImportError:
        return False
    return isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))


def _gax_transient(exc: BaseException) -> bool:
    try:
        from google.api_core import exceptions as gax_exceptions
    except ImportError:
        return False
    # RetryError has no HTTP-status mapping (it means "the SDK's own
    # internal retry budget ran out"), so it isn't caught by _status_code.
    return isinstance(exc, gax_exceptions.RetryError)


def _anthropic_transient(exc: BaseException) -> bool:
    try:
        import anthropic
    except ImportError:
        return False
    # APIConnectionError (and its APITimeoutError subclass) have no
    # status_code — a connection that never got a response.
    return isinstance(exc, anthropic.APIConnectionError)


def _anthropic_pre_dispatch(exc: BaseException) -> bool:
    """Anthropic connection failures that are pre-dispatch, not timeouts.

    The SDK wraps the underlying httpx exception with ``from err``, so
    ``exc.__cause__`` is the real httpx exception where available —
    prefer that to distinguish a connect-phase timeout from a
    read-phase one instead of treating every ``APITimeoutError`` alike.
    """
    try:
        import anthropic
    except ImportError:
        return False
    if not isinstance(exc, anthropic.APIConnectionError):
        return False
    cause = exc.__cause__
    if cause is not None and _httpx_pre_dispatch(cause):
        return True
    if cause is not None and _httpx_transient(cause):
        # A cause we can identify as httpx but that isn't connect-phase
        # (e.g. ReadTimeout) — definitely not pre-dispatch.
        return False
    # No usable cause (e.g. APIConnectionError without a wrapped httpx
    # exception): a plain connection failure with no bytes sent is the
    # common case, so default to pre-dispatch rather than the timeout
    # subclass, which defaults to False below.
    return not isinstance(exc, anthropic.APITimeoutError)


def is_transient(exc: BaseException, *, retry_status: frozenset[int]) -> bool:
    """Would a second attempt plausibly succeed?

    True for rate limits, 5xx, and connect/read timeouts across httpx,
    anthropic, and google-api-core; False for anything else (4xx other
    than 429, auth failures, ``ValueError``, programming errors).
    """
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    status = _status_code(exc)
    if status is not None:
        return status_is_transient(status, retry_status)
    return _httpx_transient(exc) or _gax_transient(exc) or _anthropic_transient(exc)


def is_pre_dispatch(exc: BaseException) -> bool:
    """Did the failure happen before any bytes reached the server?

    Connect refusal, DNS failure, and pool-wait timeouts are pre-
    dispatch: retrying is always safe regardless of what the call does.
    Everything past that point (read timeout, 5xx, a dropped connection
    mid-response) is post-dispatch — the server may have already acted
    on the request, so ``Idempotency.UNSAFE`` callers must not retry it.
    """
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError)):
        return True
    if isinstance(exc, ConnectionError) and not isinstance(exc, TimeoutError):
        return True
    if _httpx_pre_dispatch(exc):
        return True
    return _anthropic_pre_dispatch(exc)


def _gax_retry_delay_seconds(exc: BaseException) -> float | None:
    """Extract ``google.rpc.RetryInfo.retry_delay`` from a gax exception, if present.

    ``GoogleAPICallError.details`` is a list of parsed structured error-
    detail protos (``BadRequest``, ``ErrorInfo``, ``RetryInfo``, …) —
    ``RetryInfo.retry_delay`` is itself a protobuf ``Duration`` message
    (``.seconds`` + ``.nanos``), not a plain number, so it needs its own
    extraction rather than a generic attribute read.
    """
    try:
        from google.rpc import error_details_pb2
    except ImportError:
        return None
    details = getattr(exc, "details", None)
    if not details:
        return None
    for item in details:
        if isinstance(item, error_details_pb2.RetryInfo):
            duration = item.retry_delay
            return float(float(duration.seconds) + float(duration.nanos) / 1e9)
    return None


def retry_after_seconds(exc: BaseException) -> float | None:
    """Honor a server-supplied wait hint, if one is attached to ``exc``.

    Checks the ``Retry-After`` header on any exception carrying an
    httpx-shaped ``response`` (httpx, anthropic) and gax's structured
    ``RetryInfo`` proto (``google.api_core`` exceptions). Only integer-
    or-float second values are honored — the HTTP-date form of
    ``Retry-After`` is rare for these APIs and not worth the parsing
    surface.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    value: Any = None
    if headers is not None:
        getter = getattr(headers, "get", None)
        if callable(getter):
            value = getter("Retry-After") or getter("retry-after")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            pass

    return _gax_retry_delay_seconds(exc)


__all__ = [
    "is_pre_dispatch",
    "is_transient",
    "retry_after_seconds",
    "status_is_transient",
]
