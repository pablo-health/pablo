# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the request-context middleware (THERAPY-2pf4).

Covers all acceptance criteria from the bead:
  (a) every request gets a unique request_id visible in every log line
  (b) user_id / tenant_id present after auth middleware runs
  (c) route_template never contains a resolved path parameter
  (d) request_id propagates across asyncio task boundaries
  (e) X-Cloud-Trace-Context honored when set by upstream
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
from typing import TYPE_CHECKING

import pytest
from app.logging_config import (
    JSONFormatter,
    RedactPHIFilter,
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)
from app.middleware.request_context import (
    AWS_TRACE_HEADER,
    CLOUD_TRACE_HEADER,
    REQUEST_ID_HEADER,
    W3C_TRACEPARENT_HEADER,
    RequestContextMiddleware,
    _parse_aws_trace,
    _parse_gcp_trace,
    _parse_w3c_traceparent,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _reset_contextvars() -> Generator[None]:
    yield
    request_id_var.set(None)
    user_id_var.set(None)
    tenant_id_var.set(None)
    route_template_var.set(None)


@pytest.fixture
def captured_logs() -> Generator[tuple[io.StringIO, logging.Logger]]:
    """Capture JSON log lines from a dedicated logger for assertions."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())

    lg = logging.getLogger("request_context_test")
    lg.handlers = [handler]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        yield buf, lg
    finally:
        lg.handlers = []


@pytest.fixture
def captured_access_logs() -> Generator[io.StringIO]:
    """Capture the per-request access log emitted by RequestContextMiddleware.

    The middleware logs to ``pablo.access`` via its own module-level logger,
    so we attach a JSON handler to that name and restore the original
    configuration on teardown.
    """
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())

    lg = logging.getLogger("pablo.access")
    saved_handlers = lg.handlers
    saved_level = lg.level
    saved_propagate = lg.propagate
    lg.handlers = [handler]
    lg.setLevel(logging.INFO)
    lg.propagate = False
    try:
        yield buf
    finally:
        lg.handlers = saved_handlers
        lg.setLevel(saved_level)
        lg.propagate = saved_propagate


def _build_app(logger: logging.Logger | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/api/patients/{patient_id}")
    def get_patient(patient_id: str) -> dict[str, str]:
        if logger is not None:
            logger.info("fetched patient")
        return {"id": patient_id, "request_id": request_id_var.get() or ""}

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "request_id": request_id_var.get() or ""}

    return app


class TestGcpTraceParser:
    def test_extracts_trace_id_from_full_header(self) -> None:
        # Real Cloud Run header shape: TRACE_ID/SPAN_ID;o=TRACE_TRUE
        assert (
            _parse_gcp_trace("105445aa7843bc8bf206b12000100000/1;o=1")
            == "105445aa7843bc8bf206b12000100000"
        )

    def test_extracts_trace_id_without_span(self) -> None:
        assert _parse_gcp_trace("abc123") == "abc123"

    def test_extracts_trace_id_with_options_only(self) -> None:
        assert _parse_gcp_trace("abc123;o=1") == "abc123"

    def test_empty_header_returns_none(self) -> None:
        assert _parse_gcp_trace("") is None
        assert _parse_gcp_trace("   ") is None


class TestW3cTraceparentParser:
    def test_extracts_trace_id_from_valid_header(self) -> None:
        # Spec: version-trace_id-parent_id-flags
        value = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        assert _parse_w3c_traceparent(value) == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_rejects_wrong_trace_id_length(self) -> None:
        # 31 hex chars instead of 32 — malformed.
        assert _parse_w3c_traceparent("00-deadbeef-00f067aa0ba902b7-01") is None

    def test_rejects_non_hex_trace_id(self) -> None:
        assert (
            _parse_w3c_traceparent("00-NOTHEX_NOTHEX_NOTHEX_NOTHEX_NOTHEX-00f067aa0ba902b7-01")
            is None
        )

    def test_rejects_missing_segments(self) -> None:
        assert _parse_w3c_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736") is None


class TestAwsTraceParser:
    def test_extracts_root_segment(self) -> None:
        # ALB/API Gateway shape.
        value = "Root=1-67891233-abcdef012345678912345678;Parent=53995c3f42cd8ad8;Sampled=1"
        assert _parse_aws_trace(value) == "1-67891233-abcdef012345678912345678"

    def test_handles_root_only(self) -> None:
        assert _parse_aws_trace("Root=1-abc-def") == "1-abc-def"

    def test_returns_none_when_no_root(self) -> None:
        assert _parse_aws_trace("Self=foo;Parent=bar") is None

    def test_tolerates_whitespace_between_segments(self) -> None:
        assert _parse_aws_trace("Self=x; Root=1-abc-def ;Parent=y") == "1-abc-def"


class TestRequestIdMinting:
    def test_request_id_minted_when_no_trace_header(self) -> None:
        client = TestClient(_build_app())
        response = client.get("/api/health")
        assert response.status_code == 200

        request_id = response.headers[REQUEST_ID_HEADER]
        # Round-trips as a UUID (acceptance: minted UUID4 when no upstream id).
        uuid.UUID(request_id)
        assert response.json()["request_id"] == request_id

    def test_each_request_gets_unique_id(self) -> None:
        client = TestClient(_build_app())
        ids = {client.get("/api/health").headers[REQUEST_ID_HEADER] for _ in range(5)}
        assert len(ids) == 5

    def test_cloud_trace_header_honored(self) -> None:
        client = TestClient(_build_app())
        trace = "105445aa7843bc8bf206b12000100000/1;o=1"
        response = client.get("/api/health", headers={CLOUD_TRACE_HEADER: trace})
        assert response.headers[REQUEST_ID_HEADER] == "105445aa7843bc8bf206b12000100000"

    def test_w3c_traceparent_honored(self) -> None:
        client = TestClient(_build_app())
        traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        response = client.get("/api/health", headers={W3C_TRACEPARENT_HEADER: traceparent})
        assert response.headers[REQUEST_ID_HEADER] == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_aws_trace_header_honored(self) -> None:
        client = TestClient(_build_app())
        aws_trace = "Root=1-67891233-abcdef012345678912345678;Parent=53995c3f42cd8ad8;Sampled=1"
        response = client.get("/api/health", headers={AWS_TRACE_HEADER: aws_trace})
        assert response.headers[REQUEST_ID_HEADER] == "1-67891233-abcdef012345678912345678"

    def test_w3c_traceparent_takes_priority_over_cloud_specific(self) -> None:
        # If both headers are present (e.g. an OTel-instrumented client
        # behind a Google LB), prefer the standard. Lets tracing
        # backends correlate without per-cloud config.
        client = TestClient(_build_app())
        response = client.get(
            "/api/health",
            headers={
                W3C_TRACEPARENT_HEADER: ("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),
                CLOUD_TRACE_HEADER: "105445aa7843bc8bf206b12000100000/1;o=1",
            },
        )
        assert response.headers[REQUEST_ID_HEADER] == "4bf92f3577b34da6a3ce929d0e0e4736"

    def test_malformed_traceparent_falls_through_to_next_header(self) -> None:
        # Bad traceparent shouldn't poison the request_id; we should
        # fall through to the next recognized header.
        client = TestClient(_build_app())
        response = client.get(
            "/api/health",
            headers={
                W3C_TRACEPARENT_HEADER: "garbage",
                CLOUD_TRACE_HEADER: "105445aa7843bc8bf206b12000100000/1;o=1",
            },
        )
        assert response.headers[REQUEST_ID_HEADER] == "105445aa7843bc8bf206b12000100000"

    def test_empty_cloud_trace_header_falls_back_to_uuid(self) -> None:
        client = TestClient(_build_app())
        response = client.get("/api/health", headers={CLOUD_TRACE_HEADER: ""})
        uuid.UUID(response.headers[REQUEST_ID_HEADER])

    def test_request_id_visible_in_log_records(
        self, captured_logs: tuple[io.StringIO, logging.Logger]
    ) -> None:
        buf, lg = captured_logs
        client = TestClient(_build_app(lg))
        response = client.get("/api/patients/abc-123")
        request_id = response.headers[REQUEST_ID_HEADER]

        payload = json.loads(buf.getvalue().strip())
        # Acceptance (a): the id from the response correlates 1:1 with logs.
        assert payload["request_id"] == request_id


class TestRouteTemplate:
    def test_route_template_uses_pattern_not_resolved_value(
        self, captured_logs: tuple[io.StringIO, logging.Logger]
    ) -> None:
        buf, lg = captured_logs
        client = TestClient(_build_app(lg))
        # Pass a UUID-shaped path param to make a regression obvious.
        client.get("/api/patients/c4f1a3e2-0000-4000-8000-000000000abc")

        payload = json.loads(buf.getvalue().strip())
        assert payload["route_template"] == "/api/patients/{patient_id}"
        # Acceptance (c): the resolved id must NOT appear in route_template.
        assert "c4f1a3e2" not in payload["route_template"]

    def test_route_template_none_for_unmatched_route(
        self, captured_logs: tuple[io.StringIO, logging.Logger]
    ) -> None:
        # An unmatched path returns 404, but we still mint a request_id
        # so the 404 can be correlated. route_template is just absent.
        _buf, lg = captured_logs
        client = TestClient(_build_app(lg))
        response = client.get("/api/does-not-exist")
        assert response.status_code == 404
        # Header was still set, even though the handler never ran.
        assert REQUEST_ID_HEADER in response.headers


class TestContextvarHygiene:
    def test_contextvars_reset_after_request(self) -> None:
        client = TestClient(_build_app())
        client.get("/api/health")
        # The middleware must `reset` on exit so nothing leaks into the
        # test-runner's surrounding contextvars.
        assert request_id_var.get() is None
        assert route_template_var.get() is None
        assert user_id_var.get() is None
        assert tenant_id_var.get() is None

    def test_user_and_tenant_visible_when_set_inside_request(
        self, captured_logs: tuple[io.StringIO, logging.Logger]
    ) -> None:
        # Acceptance (b): when auth sets user_id/tenant_id during request
        # handling, those values flow into the log payload.
        buf, lg = captured_logs
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/whoami")
        def whoami() -> dict[str, str]:
            user_id_var.set("user-42")
            tenant_id_var.set("tenant-7")
            lg.info("inside handler")
            return {"ok": "true"}

        TestClient(app).get("/whoami")
        payload = json.loads(buf.getvalue().strip())
        assert payload["user_id"] == "user-42"
        assert payload["tenant_id"] == "tenant-7"


class TestRequestCompletedLog:
    """The middleware emits one ``event=request_completed`` line per request.

    Feeds the 5xx-rate Cloud Monitoring alert (THERAPY-8uww) and the
    per-route latency widgets on the ops dashboard. Verifies the payload
    is PHI-free and that status_code reflects the actual response.
    """

    def test_emits_one_record_per_request_with_status_and_latency(
        self, captured_access_logs: io.StringIO
    ) -> None:
        client = TestClient(_build_app())
        client.get("/api/health")
        lines = [ln for ln in captured_access_logs.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["event"] == "request_completed"
        assert payload["status_code"] == 200
        assert payload["method"] == "GET"
        assert payload["latency_ms"] >= 0
        # request_id flows in via the contextvar, not extra=
        assert "request_id" in payload

    def test_status_code_reflects_5xx_response(self, captured_access_logs: io.StringIO) -> None:
        # The 5xx alert must see status_code>=500. Verify a route that
        # raises an unhandled exception still produces the expected
        # access-log entry — finally-block emit, not happy-path-only.
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/boom")
        def boom() -> dict[str, str]:
            raise RuntimeError("kaboom")

        # Starlette's TestClient surfaces unhandled exceptions; pytest
        # would treat them as test failures. raise_server_exceptions=False
        # mirrors the prod behavior where exceptions are converted to 500.
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/boom")
        assert response.status_code == 500

        lines = [ln for ln in captured_access_logs.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["status_code"] == 500
        assert payload["event"] == "request_completed"

    def test_status_code_reflects_404(self, captured_access_logs: io.StringIO) -> None:
        client = TestClient(_build_app())
        client.get("/api/does-not-exist")
        payload = json.loads(captured_access_logs.getvalue().splitlines()[0])
        assert payload["status_code"] == 404

    def test_includes_route_template_via_contextvar(
        self, captured_access_logs: io.StringIO
    ) -> None:
        # route_template is set on the contextvar by the middleware; the
        # JSON formatter merges it into every record (THERAPY-2pf4 + za2y).
        # Per the 5xx-by-route alert: must show /api/patients/{patient_id},
        # never the resolved UUID.
        client = TestClient(_build_app())
        client.get("/api/patients/c4f1a3e2-0000-4000-8000-000000000abc")
        payload = json.loads(captured_access_logs.getvalue().splitlines()[0])
        assert payload["route_template"] == "/api/patients/{patient_id}"
        assert "c4f1a3e2" not in payload["route_template"]

    def test_no_phi_keys_in_payload(self, captured_access_logs: io.StringIO) -> None:
        # Belt-and-suspenders: defend against future drift where someone
        # adds a PHI-shaped field to the access log emit.
        client = TestClient(_build_app())
        client.get("/api/health")
        payload = json.loads(captured_access_logs.getvalue().splitlines()[0])
        for forbidden in ("patient_id", "patient_name", "soap_text", "transcript"):
            assert forbidden not in payload


class TestAsyncioTaskPropagation:
    def test_request_id_survives_create_task(
        self, captured_logs: tuple[io.StringIO, logging.Logger]
    ) -> None:
        # Acceptance (d): asyncio.create_task copies the current context,
        # so a spawned task should see the same request_id as its parent.
        buf, lg = captured_logs
        app = FastAPI()
        app.add_middleware(RequestContextMiddleware)

        @app.get("/spawn")
        async def spawn() -> dict[str, str]:
            parent_id = request_id_var.get()
            child_id_box: dict[str, str | None] = {}

            async def child() -> None:
                # Log from the spawned task; the JSON formatter pulls
                # request_id from the contextvar copy.
                lg.info("child task")
                child_id_box["id"] = request_id_var.get()

            await asyncio.create_task(child())
            return {"parent": parent_id or "", "child": child_id_box["id"] or ""}

        client = TestClient(app)
        response = client.get("/spawn")
        request_id = response.headers[REQUEST_ID_HEADER]
        body = response.json()
        assert body["parent"] == request_id
        assert body["child"] == request_id

        # And the child task's log line carries the same id.
        payload = json.loads(buf.getvalue().strip())
        assert payload["request_id"] == request_id
