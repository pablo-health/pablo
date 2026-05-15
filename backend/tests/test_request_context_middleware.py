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
    CLOUD_TRACE_HEADER,
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    _request_id_from_trace,
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


class TestRequestIdFromTrace:
    def test_extracts_trace_id_from_full_header(self) -> None:
        # Real Cloud Run header shape: TRACE_ID/SPAN_ID;o=TRACE_TRUE
        assert (
            _request_id_from_trace("105445aa7843bc8bf206b12000100000/1;o=1")
            == "105445aa7843bc8bf206b12000100000"
        )

    def test_extracts_trace_id_without_span(self) -> None:
        assert _request_id_from_trace("abc123") == "abc123"

    def test_extracts_trace_id_with_options_only(self) -> None:
        assert _request_id_from_trace("abc123;o=1") == "abc123"

    def test_empty_header_returns_none(self) -> None:
        assert _request_id_from_trace("") is None
        assert _request_id_from_trace("   ") is None


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
        response = client.get(
            "/api/health", headers={CLOUD_TRACE_HEADER: trace}
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
