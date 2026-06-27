# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for Cloud Tasks trace propagation (THERAPY-2pf4).

When a Cloud Task is enqueued from inside a request, its HTTP target
should carry trace headers built from the originating request_id. The
inbound RequestContextMiddleware on the receiving handler will then
adopt that request_id automatically, stitching the async work back
into the originating request's log trail without any handler-side
changes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.logging_config import request_id_var
from app.middleware.request_context import REQUEST_ID_HEADER, W3C_TRACEPARENT_HEADER
from app.services.cloud_tasks_service import _trace_propagation_headers

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _reset_contextvars() -> Generator[None]:
    yield
    request_id_var.set(None)


class TestTracePropagationHeaders:
    def test_empty_when_no_request_id_bound(self) -> None:
        # Cloud Tasks enqueued from startup hooks or cron jobs have no
        # originating user request — return no headers rather than
        # synthesize a fake trace.
        assert _trace_propagation_headers() == {}

    def test_includes_request_id_and_traceparent_when_w3c_shaped(self) -> None:
        request_id_var.set("105445aa7843bc8bf206b12000100000")
        headers = _trace_propagation_headers()
        assert headers[REQUEST_ID_HEADER] == "105445aa7843bc8bf206b12000100000"
        # traceparent for a 32-hex trace_id has the shape
        # 00-<trace_id>-<16hex span_id>-<2hex flags>
        assert headers[W3C_TRACEPARENT_HEADER].startswith("00-105445aa7843bc8bf206b12000100000-")

    def test_uuid_request_id_yields_traceparent(self) -> None:
        # Default-minted UUID4 ids should still produce a valid
        # traceparent (dashes are stripped during coercion).
        request_id_var.set("c4f1a3e2-0000-4000-8000-000000000abc")
        headers = _trace_propagation_headers()
        assert W3C_TRACEPARENT_HEADER in headers
        # The flattened, lowercase hex form is what flows into the
        # traceparent's trace_id field.
        assert "c4f1a3e2000040008000000000000abc" in headers[W3C_TRACEPARENT_HEADER]

    def test_aws_shaped_id_falls_back_to_request_id_only(self) -> None:
        # AWS Root values can't be expressed as W3C traceparent — we
        # still propagate X-Request-Id (which works portably) but skip
        # the malformed traceparent rather than ship garbage.
        request_id_var.set("1-67891233-abcdef012345678912345678")
        headers = _trace_propagation_headers()
        assert headers[REQUEST_ID_HEADER] == "1-67891233-abcdef012345678912345678"
        assert W3C_TRACEPARENT_HEADER not in headers
