# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the auth-failed structured log emit on 401/403 responses.

The handler in ``app.api_errors`` piggy-backs an ``event=auth_failed``
record onto every 401/403 ``HTTPException`` so Cloud Monitoring can
build a logs-based metric and alert on auth-failure spikes from a
single source IP (THERAPY-8uww).

The handler must:
- emit on 401 and 403, with the envelope ``error.code`` as ``reason``;
- skip ``MFA_REQUIRED`` (expected first-login flow, not a brute-force
  signal);
- not emit on 200 / 400 / 404 / 500;
- never break the response — a logging error is swallowed.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest
from app.api_errors import register_exception_handlers
from app.logging_config import JSONFormatter, RedactPHIFilter
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def auth_logs() -> Generator[io.StringIO]:
    """Capture JSON log lines from the ``pablo.auth`` logger."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())

    lg = logging.getLogger("pablo.auth")
    saved_handlers = lg.handlers
    saved_level = lg.level
    saved_propagate = lg.propagate
    lg.handlers = [handler]
    lg.setLevel(logging.WARNING)
    lg.propagate = False
    try:
        yield buf
    finally:
        lg.handlers = saved_handlers
        lg.setLevel(saved_level)
        lg.propagate = saved_propagate


def _build_app() -> FastAPI:
    """A minimal app that raises HTTPException in the shapes auth uses."""
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/ok")
    def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/unauth-token-expired")
    def unauth_token_expired() -> None:
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "TOKEN_EXPIRED", "message": "...", "details": {}}},
        )

    @app.get("/forbidden-no-practice")
    def forbidden_no_practice() -> None:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "NO_PRACTICE", "message": "...", "details": {}}},
        )

    @app.get("/forbidden-mfa-required")
    def forbidden_mfa_required() -> None:
        # MFA_REQUIRED is the legitimate first-login state; it must NOT
        # increment the auth-failed counter.
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "MFA_REQUIRED", "message": "...", "details": {}}},
        )

    @app.get("/notfound")
    def notfound() -> None:
        raise HTTPException(status_code=404, detail="nope")

    @app.get("/badrequest")
    def badrequest() -> None:
        raise HTTPException(status_code=400, detail="nope")

    @app.get("/unauth-string-detail")
    def unauth_string_detail() -> None:
        # Legacy: not all sites use the envelope. The handler should
        # still emit, with reason=UNKNOWN.
        raise HTTPException(status_code=401, detail="who?")

    @app.get("/boom")
    def boom() -> None:
        # An unexpected, non-API exception (e.g. a DB driver error). It
        # must be caught by the catch-all handler, logged as one record,
        # and rendered as a generic 500 envelope.
        raise RuntimeError("kaboom")

    return app


@pytest.fixture
def unhandled_logs() -> Generator[io.StringIO]:
    """Capture JSON log lines from the ``pablo.unhandled`` logger."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JSONFormatter())
    handler.addFilter(RedactPHIFilter())

    lg = logging.getLogger("pablo.unhandled")
    saved_handlers = lg.handlers
    saved_level = lg.level
    saved_propagate = lg.propagate
    lg.handlers = [handler]
    lg.setLevel(logging.ERROR)
    lg.propagate = False
    try:
        yield buf
    finally:
        lg.handlers = saved_handlers
        lg.setLevel(saved_level)
        lg.propagate = saved_propagate


def test_unhandled_exception_returns_500_envelope(unhandled_logs: io.StringIO) -> None:
    # raise_server_exceptions=False so the TestClient lets the app's
    # exception handler run instead of re-raising into the test.
    client = TestClient(_build_app(), raise_server_exceptions=False)
    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred", "details": {}}
    }
    # The internal message must never leak to the client.
    assert "kaboom" not in response.text

    # Exactly one structured record, with the class and full traceback in
    # a single entry (the whole point — no fragmented stderr dump).
    lines = _lines(unhandled_logs)
    assert len(lines) == 1
    record = lines[0]
    assert record["message"] == "unhandled_exception"
    assert record["error_class"] == "RuntimeError"
    assert "Traceback (most recent call last)" in record["exc_info"]
    assert record["http_method"] == "GET"


def _lines(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]


def test_emits_on_401_with_envelope_reason(auth_logs: io.StringIO) -> None:
    response = TestClient(_build_app()).get("/unauth-token-expired")
    assert response.status_code == 401
    # Default FastAPI envelope shape must be preserved.
    assert response.json() == {
        "detail": {"error": {"code": "TOKEN_EXPIRED", "message": "...", "details": {}}}
    }
    payloads = _lines(auth_logs)
    assert len(payloads) == 1
    assert payloads[0]["event"] == "auth_failed"
    assert payloads[0]["reason"] == "TOKEN_EXPIRED"
    assert payloads[0]["status_code"] == 401


def test_emits_on_403_with_envelope_reason(auth_logs: io.StringIO) -> None:
    TestClient(_build_app()).get("/forbidden-no-practice")
    payloads = _lines(auth_logs)
    assert len(payloads) == 1
    assert payloads[0]["reason"] == "NO_PRACTICE"
    assert payloads[0]["status_code"] == 403


def test_skips_mfa_required(auth_logs: io.StringIO) -> None:
    response = TestClient(_build_app()).get("/forbidden-mfa-required")
    assert response.status_code == 403
    # Response is unchanged...
    assert response.json()["detail"]["error"]["code"] == "MFA_REQUIRED"
    # ...but the auth-failed counter is NOT incremented.
    assert _lines(auth_logs) == []


def test_no_emit_on_404(auth_logs: io.StringIO) -> None:
    TestClient(_build_app()).get("/notfound")
    assert _lines(auth_logs) == []


def test_no_emit_on_400(auth_logs: io.StringIO) -> None:
    TestClient(_build_app()).get("/badrequest")
    assert _lines(auth_logs) == []


def test_no_emit_on_200(auth_logs: io.StringIO) -> None:
    TestClient(_build_app()).get("/ok")
    assert _lines(auth_logs) == []


def test_string_detail_falls_back_to_unknown_reason(auth_logs: io.StringIO) -> None:
    TestClient(_build_app()).get("/unauth-string-detail")
    payloads = _lines(auth_logs)
    assert len(payloads) == 1
    assert payloads[0]["reason"] == "UNKNOWN"


def test_source_ip_recorded(auth_logs: io.StringIO) -> None:
    # TestClient sets the client address to "testclient" (127.0.0.1) so
    # we just verify the field is populated, not its exact value.
    TestClient(_build_app()).get("/unauth-token-expired")
    payloads = _lines(auth_logs)
    assert "source_ip" in payloads[0]


def test_x_forwarded_for_used_when_present(auth_logs: io.StringIO) -> None:
    # The trusted proxy (Cloud Run) appends the real client IP on the RIGHT;
    # a client-supplied leftmost value is spoofable and must be ignored.
    TestClient(_build_app()).get(
        "/unauth-token-expired",
        headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"},
    )
    payloads = _lines(auth_logs)
    assert payloads[0]["source_ip"] == "203.0.113.7"
