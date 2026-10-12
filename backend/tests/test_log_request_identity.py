# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Error logs carry the request's tenant and user ids.

Sync FastAPI dependencies (which is what the auth chain is) run in a worker
thread on a COPY of the request's context, so an id bound there used to vanish
before the route body, the error handlers or the access line logged anything.
These tests drive real requests through ``RequestContextMiddleware`` and run
every record through the real ``JSONFormatter`` + ``RedactPHIFilter``, so what
they assert is the JSON an operator would actually filter on.
"""

from __future__ import annotations

import asyncio
import contextvars
import io
import json
import logging
import threading
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from app.api_errors import register_exception_handlers
from app.auth.service import PROVISIONING_UNAVAILABLE_EVENT, _await_provisioning_ready
from app.db.middleware import _resolve_schema_from_request
from app.logging_config import (
    JSONFormatter,
    RedactPHIFilter,
    begin_request_log_identity,
    bind_log_tenant_id,
    bind_log_user_id,
    current_log_tenant_id,
    current_log_user_id,
    end_request_log_identity,
    request_id_var,
    route_template_var,
    tenant_id_var,
    user_id_var,
)
from app.middleware.request_context import RequestContextMiddleware
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator


@pytest.fixture(autouse=True)
def _reset_contextvars() -> Generator[None]:
    yield
    request_id_var.set(None)
    user_id_var.set(None)
    tenant_id_var.set(None)
    route_template_var.set(None)


@contextmanager
def _json_logs(name: str, level: int = logging.DEBUG) -> Iterator[io.StringIO]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())
    lg = logging.getLogger(name)
    saved = (lg.handlers, lg.level, lg.propagate)
    lg.handlers = [handler]
    lg.setLevel(level)
    lg.propagate = False
    try:
        yield buf
    finally:
        lg.handlers, lg.level, lg.propagate = saved


def _records(buf: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


_ROUTE_LOGGER = "log_identity_test"


def _sync_auth(user_id: str, tenant_id: str) -> Any:
    """A sync dependency, like the real auth chain: it runs in a worker thread."""

    def dependency() -> None:
        bind_log_user_id(user_id)
        bind_log_tenant_id(tenant_id)

    return dependency


def _build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    lg = logging.getLogger(_ROUTE_LOGGER)

    @app.get("/authed", dependencies=[Depends(_sync_auth("user-42", "practice-7"))])
    async def authed() -> dict[str, str]:
        lg.error("route_failed")
        return {"ok": "true"}

    @app.get("/anonymous")
    async def anonymous() -> dict[str, str]:
        lg.error("route_failed")
        return {"ok": "true"}

    @app.get("/authed-boom", dependencies=[Depends(_sync_auth("user-42", "practice-7"))])
    async def authed_boom() -> dict[str, str]:
        raise RuntimeError("kaboom")

    @app.get("/who/{user}")
    async def who(user: str) -> dict[str, str]:
        # Bound on the loop, then yields so the other request interleaves.
        bind_log_user_id(user)
        bind_log_tenant_id(f"practice-{user}")
        await asyncio.sleep(0.01)
        lg.error("route_failed")
        return {"ok": "true"}

    return app


class TestIdsReachAsyncRouteLogs:
    def test_ids_bound_in_a_sync_dependency_reach_the_async_route(self) -> None:
        with _json_logs(_ROUTE_LOGGER) as buf:
            TestClient(_build_app()).get("/authed")

        (record,) = _records(buf)
        assert record["level"] == "ERROR"
        assert record["user_id"] == "user-42"
        assert record["tenant_id"] == "practice-7"

    def test_ids_reach_the_access_line(self) -> None:
        with _json_logs("pablo.access") as buf:
            TestClient(_build_app()).get("/authed")

        (record,) = _records(buf)
        assert record["event"] == "request_completed"
        assert record["user_id"] == "user-42"
        assert record["tenant_id"] == "practice-7"

    def test_unhandled_500_carries_ids_and_request_id(self) -> None:
        with _json_logs("pablo.unhandled") as buf:
            response = TestClient(_build_app(), raise_server_exceptions=False).get(
                "/authed-boom", headers={"X-Cloud-Trace-Context": "trace-abc/1;o=1"}
            )

        assert response.status_code == 500
        (record,) = _records(buf)
        assert record["message"] == "unhandled_exception"
        assert record["user_id"] == "user-42"
        assert record["tenant_id"] == "practice-7"
        assert record["request_id"] == "trace-abc"


class TestNoBleedBetweenRequests:
    def test_next_request_starts_without_the_previous_ids(self) -> None:
        client = TestClient(_build_app())
        with _json_logs(_ROUTE_LOGGER) as buf:
            client.get("/authed")
            client.get("/anonymous")

        first, second = _records(buf)
        assert first["user_id"] == "user-42"
        assert "user_id" not in second
        assert "tenant_id" not in second

    def test_nothing_survives_the_request(self) -> None:
        TestClient(_build_app()).get("/authed")
        assert current_log_user_id() is None
        assert current_log_tenant_id() is None

    def test_concurrent_requests_keep_their_own_ids(self) -> None:
        async def run() -> None:
            transport = httpx.ASGITransport(app=_build_app())
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                await asyncio.gather(client.get("/who/alice"), client.get("/who/bob"))

        with _json_logs(_ROUTE_LOGGER) as buf:
            asyncio.run(run())

        records = _records(buf)
        assert len(records) == 2
        assert {(r["user_id"], r["tenant_id"]) for r in records} == {
            ("alice", "practice-alice"),
            ("bob", "practice-bob"),
        }


class TestTenantBoundWhereTheSchemaResolves:
    def test_tenant_bound_in_the_worker_thread_is_visible_to_the_request(self) -> None:
        """The database middleware resolves the practice inside run_in_threadpool."""
        request = MagicMock()
        request.state = SimpleNamespace(
            verified_identity=SimpleNamespace(email="clinician@example.com")
        )
        _identity, token = begin_request_log_identity()
        try:
            with patch(
                "app.auth.service._resolve_practice_from_email",
                return_value=("practice-9", "practice_9"),
            ):
                ctx = contextvars.copy_context()
                worker = threading.Thread(
                    target=ctx.run, args=(_resolve_schema_from_request, request)
                )
                worker.start()
                worker.join()
            assert current_log_tenant_id() == "practice-9"
        finally:
            end_request_log_identity(token)


@contextmanager
def _practice_row(status: str) -> Iterator[None]:
    db = MagicMock()
    db.get.return_value = SimpleNamespace(provisioning_status=status)
    session = MagicMock()
    session.__enter__.return_value = db
    with (
        patch("app.db.create_standalone_session", return_value=session),
        patch("time.sleep"),
    ):
        yield


class TestProvisioningUnavailableIsLogged:
    def test_failed_provisioning_logs_an_error_with_the_marker(self) -> None:
        _identity, token = begin_request_log_identity()
        try:
            bind_log_tenant_id("practice-stuck")
            with (
                _json_logs("app.auth.service") as buf,
                _practice_row("failed"),
                pytest.raises(HTTPException) as raised,
            ):
                _await_provisioning_ready("practice-stuck")
        finally:
            end_request_log_identity(token)

        assert raised.value.status_code == 503
        (record,) = _records(buf)
        assert record["level"] == "ERROR"
        assert record["event"] == PROVISIONING_UNAVAILABLE_EVENT == "provisioning_unavailable"
        assert record["provisioning_status"] == "failed"
        assert record["practice_id"] == "practice-stuck"
        assert record["tenant_id"] == "practice-stuck"

    def test_still_in_progress_logs_the_same_marker_as_a_warning(self) -> None:
        with (
            _json_logs("app.auth.service") as buf,
            _practice_row("in_progress"),
            pytest.raises(HTTPException) as raised,
        ):
            _await_provisioning_ready("practice-new")

        assert raised.value.status_code == 503
        (record,) = _records(buf)
        assert record["level"] == "WARNING"
        assert record["event"] == PROVISIONING_UNAVAILABLE_EVENT
        assert record["provisioning_status"] == "in_progress"
        assert record["practice_id"] == "practice-new"

    def test_ready_practice_logs_nothing(self) -> None:
        with _json_logs("app.auth.service") as buf, _practice_row("ready"):
            _await_provisioning_ready("practice-ok")
        assert _records(buf) == []
