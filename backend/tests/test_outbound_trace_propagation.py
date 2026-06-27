# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for outbound trace propagation (THERAPY-2pf4).

Verifies that the request_id minted on an inbound request rides along
on outbound httpx calls as ``X-Request-Id`` and ``traceparent``, so
downstream services (AssemblyAI, Firebase, internal callbacks) can be
correlated to the user-facing request that triggered them.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING

import httpx
import pytest
from app.logging_config import request_id_var
from app.middleware import REQUEST_ID_HEADER, W3C_TRACEPARENT_HEADER
from app.middleware.outbound import (
    _trace_id_for_w3c,
    build_traceparent,
    inject_trace_headers,
    tracing_async_client,
)

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _reset_contextvars() -> Generator[None]:
    yield
    request_id_var.set(None)


class TestTraceIdCoercion:
    def test_uuid_with_dashes_collapses_to_32_hex(self) -> None:
        rid = "c4f1a3e2-0000-4000-8000-000000000abc"
        assert _trace_id_for_w3c(rid) == "c4f1a3e2000040008000000000000abc"

    def test_already_32_hex_passes_through(self) -> None:
        rid = "105445aa7843bc8bf206b12000100000"
        assert _trace_id_for_w3c(rid) == rid

    def test_aws_root_value_rejected(self) -> None:
        # AWS X-Ray Root values aren't W3C-shaped without conversion.
        # We refuse rather than ship a malformed traceparent.
        assert _trace_id_for_w3c("1-67891233-abcdef012345678912345678") is None

    def test_non_hex_rejected(self) -> None:
        assert _trace_id_for_w3c("definitely not a trace id") is None


class TestBuildTraceparent:
    def test_well_formed_for_uuid(self) -> None:
        rid = str(uuid.uuid4())
        tp = build_traceparent(rid)
        assert tp is not None
        # Shape: 00-<32hex>-<16hex>-<2hex flags>
        assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}", tp), tp

    def test_returns_none_for_non_w3c_id(self) -> None:
        assert build_traceparent("1-67891233-abcdef012345678912345678") is None

    def test_fresh_span_id_per_call(self) -> None:
        rid = str(uuid.uuid4())
        a = build_traceparent(rid)
        b = build_traceparent(rid)
        assert a is not None
        assert b is not None
        # Same trace_id, different span_id.
        assert a.split("-")[1] == b.split("-")[1]
        assert a.split("-")[2] != b.split("-")[2]


class TestInjectTraceHeaders:
    @pytest.mark.anyio
    async def test_injects_when_request_id_set(self) -> None:
        request_id_var.set("105445aa7843bc8bf206b12000100000")
        req = httpx.Request("GET", "https://example.com/")
        await inject_trace_headers(req)
        assert req.headers[REQUEST_ID_HEADER] == "105445aa7843bc8bf206b12000100000"
        assert req.headers[W3C_TRACEPARENT_HEADER].startswith(
            "00-105445aa7843bc8bf206b12000100000-"
        )

    @pytest.mark.anyio
    async def test_noop_when_no_request_id_bound(self) -> None:
        # Background tasks / startup hooks have no request scope.
        req = httpx.Request("GET", "https://example.com/")
        await inject_trace_headers(req)
        assert REQUEST_ID_HEADER not in req.headers
        assert W3C_TRACEPARENT_HEADER not in req.headers

    @pytest.mark.anyio
    async def test_does_not_overwrite_caller_set_headers(self) -> None:
        # If a caller is explicitly forwarding an upstream trace, the
        # caller wins — we don't clobber their decision.
        request_id_var.set("105445aa7843bc8bf206b12000100000")
        req = httpx.Request(
            "GET",
            "https://example.com/",
            headers={
                REQUEST_ID_HEADER: "caller-set",
                W3C_TRACEPARENT_HEADER: "00-deadbeef-cafe-01",
            },
        )
        await inject_trace_headers(req)
        assert req.headers[REQUEST_ID_HEADER] == "caller-set"
        assert req.headers[W3C_TRACEPARENT_HEADER] == "00-deadbeef-cafe-01"

    @pytest.mark.anyio
    async def test_sets_request_id_when_id_is_not_w3c_shape(self) -> None:
        # AWS-shaped id — we can't synthesize a traceparent but the
        # portable X-Request-Id still propagates for correlation.
        request_id_var.set("1-67891233-abcdef012345678912345678")
        req = httpx.Request("GET", "https://example.com/")
        await inject_trace_headers(req)
        assert req.headers[REQUEST_ID_HEADER] == "1-67891233-abcdef012345678912345678"
        assert W3C_TRACEPARENT_HEADER not in req.headers


class TestTracingAsyncClient:
    @pytest.mark.anyio
    async def test_outbound_request_carries_headers(self) -> None:
        captured: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(dict(request.headers))
            return httpx.Response(200, json={"ok": True})

        request_id_var.set("105445aa7843bc8bf206b12000100000")
        transport = httpx.MockTransport(handler)
        async with tracing_async_client(transport=transport) as client:
            await client.get("https://api.example.com/v1/thing")

        assert captured[REQUEST_ID_HEADER.lower()] == "105445aa7843bc8bf206b12000100000"
        assert captured[W3C_TRACEPARENT_HEADER].startswith("00-105445aa7843bc8bf206b12000100000-")

    @pytest.mark.anyio
    async def test_preserves_caller_supplied_event_hooks(self) -> None:
        # A caller's own request hook should still fire (e.g. for
        # auth, retries, metrics) — our hook is appended, not
        # exclusive.
        caller_hook_calls = 0

        async def caller_hook(_request: httpx.Request) -> None:
            nonlocal caller_hook_calls
            caller_hook_calls += 1

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(204)

        request_id_var.set("105445aa7843bc8bf206b12000100000")
        async with tracing_async_client(
            transport=httpx.MockTransport(handler),
            event_hooks={"request": [caller_hook]},
        ) as client:
            await client.get("https://api.example.com/")

        assert caller_hook_calls == 1


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
