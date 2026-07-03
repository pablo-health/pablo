# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Table-driven tests for the cross-SDK transient-failure classifier.

Covers httpx (direct and anthropic-wrapped), google.api_core (gax), and
stdlib exception shapes. SDK imports are real (all three are installed
dependencies), matching how ``classify.py`` itself imports them —
nothing here is faked.
"""

from __future__ import annotations

import anthropic
import httpx
import pytest
from app.reliability.classify import (
    is_pre_dispatch,
    is_transient,
    retry_after_seconds,
    status_is_transient,
)
from google.api_core import exceptions as gax_exceptions
from google.rpc import error_details_pb2

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def _httpx_request() -> httpx.Request:
    return httpx.Request("POST", "https://example.test/v1/generate")


def _httpx_response(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status_code, request=_httpx_request(), headers=headers)


def _anthropic_status_error(
    cls: type[anthropic.APIStatusError],
    *,
    status_code: int = 500,
    headers: dict[str, str] | None = None,
) -> anthropic.APIStatusError:
    # Most subclasses pin a literal `status_code` class attribute; the plain
    # `InternalServerError` doesn't, so it takes it from the response instead.
    response = _httpx_response(getattr(cls, "status_code", status_code), headers=headers)
    return cls("boom", response=response, body=None)


class TestStatusIsTransient:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_default_retry_statuses_are_transient(self, status: int) -> None:
        assert status_is_transient(status, _RETRY_STATUS) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
    def test_client_errors_are_not_transient(self, status: int) -> None:
        assert status_is_transient(status, _RETRY_STATUS) is False


class TestIsTransient:
    # --- httpx -----------------------------------------------------------

    def test_httpx_connect_error_is_transient(self) -> None:
        assert is_transient(httpx.ConnectError("refused"), retry_status=_RETRY_STATUS) is True

    def test_httpx_read_timeout_is_transient(self) -> None:
        assert is_transient(httpx.ReadTimeout("slow"), retry_status=_RETRY_STATUS) is True

    def test_httpx_pool_timeout_is_transient(self) -> None:
        assert is_transient(httpx.PoolTimeout("busy"), retry_status=_RETRY_STATUS) is True

    def test_httpx_503_status_error_is_transient(self) -> None:
        exc = httpx.HTTPStatusError("503", request=_httpx_request(), response=_httpx_response(503))
        assert is_transient(exc, retry_status=_RETRY_STATUS) is True

    def test_httpx_400_status_error_is_not_transient(self) -> None:
        exc = httpx.HTTPStatusError("400", request=_httpx_request(), response=_httpx_response(400))
        assert is_transient(exc, retry_status=_RETRY_STATUS) is False

    # --- gax (google.api_core) --------------------------------------------

    def test_gax_service_unavailable_is_transient(self) -> None:
        assert is_transient(gax_exceptions.ServiceUnavailable("x"), retry_status=_RETRY_STATUS)

    def test_gax_deadline_exceeded_is_transient(self) -> None:
        assert is_transient(gax_exceptions.DeadlineExceeded("x"), retry_status=_RETRY_STATUS)

    def test_gax_retry_error_is_transient(self) -> None:
        # RetryError has no HTTP-status mapping — exercised separately
        # from the status-code path.
        assert is_transient(gax_exceptions.RetryError("x", cause=None), retry_status=_RETRY_STATUS)

    def test_gax_permission_denied_is_not_transient(self) -> None:
        assert not is_transient(gax_exceptions.PermissionDenied("x"), retry_status=_RETRY_STATUS)

    def test_gax_not_found_is_not_transient(self) -> None:
        assert not is_transient(gax_exceptions.NotFound("x"), retry_status=_RETRY_STATUS)

    # --- anthropic ---------------------------------------------------------

    def test_anthropic_connection_error_is_transient(self) -> None:
        exc = anthropic.APIConnectionError(request=_httpx_request())
        assert is_transient(exc, retry_status=_RETRY_STATUS) is True

    def test_anthropic_timeout_error_is_transient(self) -> None:
        exc = anthropic.APITimeoutError(request=_httpx_request())
        assert is_transient(exc, retry_status=_RETRY_STATUS) is True

    def test_anthropic_rate_limit_is_transient(self) -> None:
        exc = _anthropic_status_error(anthropic.RateLimitError)
        assert is_transient(exc, retry_status=_RETRY_STATUS) is True

    def test_anthropic_internal_server_error_is_transient(self) -> None:
        exc = _anthropic_status_error(anthropic.InternalServerError)
        assert is_transient(exc, retry_status=_RETRY_STATUS) is True

    def test_anthropic_bad_request_is_not_transient(self) -> None:
        exc = _anthropic_status_error(anthropic.BadRequestError)
        assert is_transient(exc, retry_status=_RETRY_STATUS) is False

    def test_anthropic_authentication_error_is_not_transient(self) -> None:
        exc = _anthropic_status_error(anthropic.AuthenticationError)
        assert is_transient(exc, retry_status=_RETRY_STATUS) is False

    # --- stdlib ------------------------------------------------------------

    def test_stdlib_timeout_error_is_transient(self) -> None:
        assert is_transient(TimeoutError("slow"), retry_status=_RETRY_STATUS) is True

    def test_stdlib_connection_error_is_transient(self) -> None:
        assert is_transient(ConnectionError("refused"), retry_status=_RETRY_STATUS) is True

    def test_value_error_is_not_transient(self) -> None:
        assert is_transient(ValueError("bad input"), retry_status=_RETRY_STATUS) is False

    def test_import_error_is_not_transient(self) -> None:
        assert is_transient(ImportError("missing"), retry_status=_RETRY_STATUS) is False


class TestIsPreDispatch:
    def test_httpx_connect_error_is_pre_dispatch(self) -> None:
        assert is_pre_dispatch(httpx.ConnectError("refused")) is True

    def test_httpx_connect_timeout_is_pre_dispatch(self) -> None:
        assert is_pre_dispatch(httpx.ConnectTimeout("slow connect")) is True

    def test_httpx_pool_timeout_is_pre_dispatch(self) -> None:
        assert is_pre_dispatch(httpx.PoolTimeout("busy pool")) is True

    def test_httpx_read_timeout_is_not_pre_dispatch(self) -> None:
        # Bytes were already sent — the server may have acted on them.
        assert is_pre_dispatch(httpx.ReadTimeout("slow read")) is False

    def test_httpx_write_timeout_is_not_pre_dispatch(self) -> None:
        assert is_pre_dispatch(httpx.WriteTimeout("slow write")) is False

    def test_httpx_status_error_is_not_pre_dispatch(self) -> None:
        exc = httpx.HTTPStatusError("503", request=_httpx_request(), response=_httpx_response(503))
        assert is_pre_dispatch(exc) is False

    def test_connection_refused_is_pre_dispatch(self) -> None:
        assert is_pre_dispatch(ConnectionRefusedError("refused")) is True

    def test_bare_connection_error_is_pre_dispatch(self) -> None:
        assert is_pre_dispatch(ConnectionError("refused")) is True

    def test_bare_timeout_error_is_not_pre_dispatch(self) -> None:
        # Ambiguous connect-vs-read; conservative default is False.
        assert is_pre_dispatch(TimeoutError("slow")) is False

    def test_gax_service_unavailable_is_not_pre_dispatch(self) -> None:
        # gax exceptions are response-classified, never raw connect errors.
        assert is_pre_dispatch(gax_exceptions.ServiceUnavailable("x")) is False

    def test_anthropic_connection_error_with_connect_cause_is_pre_dispatch(self) -> None:
        try:
            raise httpx.ConnectError("refused")
        except httpx.ConnectError as cause:
            exc = anthropic.APIConnectionError(request=_httpx_request())
            exc.__cause__ = cause
        assert is_pre_dispatch(exc) is True

    def test_anthropic_connection_error_with_read_timeout_cause_is_not_pre_dispatch(self) -> None:
        try:
            raise httpx.ReadTimeout("slow")
        except httpx.ReadTimeout as cause:
            exc = anthropic.APIConnectionError(request=_httpx_request())
            exc.__cause__ = cause
        assert is_pre_dispatch(exc) is False

    def test_anthropic_connection_error_without_cause_defaults_pre_dispatch(self) -> None:
        exc = anthropic.APIConnectionError(request=_httpx_request())
        assert is_pre_dispatch(exc) is True

    def test_anthropic_timeout_error_without_cause_is_not_pre_dispatch(self) -> None:
        exc = anthropic.APITimeoutError(request=_httpx_request())
        assert is_pre_dispatch(exc) is False


class TestRetryAfterSeconds:
    def test_httpx_status_error_retry_after_header(self) -> None:
        exc = httpx.HTTPStatusError(
            "429",
            request=_httpx_request(),
            response=_httpx_response(429, headers={"Retry-After": "3"}),
        )
        assert retry_after_seconds(exc) == pytest.approx(3.0)

    def test_anthropic_status_error_retry_after_header(self) -> None:
        exc = _anthropic_status_error(anthropic.RateLimitError, headers={"Retry-After": "7"})
        assert retry_after_seconds(exc) == pytest.approx(7.0)

    def test_no_header_returns_none(self) -> None:
        exc = httpx.HTTPStatusError("503", request=_httpx_request(), response=_httpx_response(503))
        assert retry_after_seconds(exc) is None

    def test_non_numeric_header_returns_none(self) -> None:
        exc = httpx.HTTPStatusError(
            "429",
            request=_httpx_request(),
            response=_httpx_response(429, headers={"Retry-After": "Wed, 01 Jan 2026 00:00:00 GMT"}),
        )
        assert retry_after_seconds(exc) is None

    def test_plain_exception_returns_none(self) -> None:
        assert retry_after_seconds(ValueError("no retry info here")) is None

    def test_gax_retry_info_duration_is_honored(self) -> None:
        # RetryInfo.retry_delay is a protobuf Duration (seconds + nanos),
        # not a plain number — exercise the real proto shape gax attaches
        # to `.details`, not a bolted-on attribute.
        retry_info = error_details_pb2.RetryInfo()
        retry_info.retry_delay.seconds = 4
        retry_info.retry_delay.nanos = 500_000_000
        exc = gax_exceptions.ServiceUnavailable("slow down", details=[retry_info])
        assert retry_after_seconds(exc) == pytest.approx(4.5)

    def test_gax_exception_without_retry_info_returns_none(self) -> None:
        exc = gax_exceptions.ServiceUnavailable("no retry info here")
        assert retry_after_seconds(exc) is None
