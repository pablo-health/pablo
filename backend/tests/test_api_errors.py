# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the auth-failed structured log emit on 401/403 responses.

The handler in ``app.api_errors`` piggy-backs an ``event=auth_failed``
record onto every 401/403 ``HTTPException`` so Cloud Monitoring can
build a logs-based metric and alert on auth-failure spikes from a
single source IP (THERAPY-8uww).

The handler must:
- emit on 401 and 403, with the envelope ``error.code`` as ``reason``;
- skip expected, benign session states that are not a brute-force signal —
  ``MFA_REQUIRED`` (first-login), ``IDLE_TIMEOUT`` (session aged out) and
  ``TOKEN_EXPIRED`` (client behind on token refresh);
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

    @app.get("/unauth-invalid-token")
    def unauth_invalid_token() -> None:
        # A forged / malformed token — the brute-force signal the counter
        # exists to catch. It MUST increment.
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "INVALID_TOKEN", "message": "...", "details": {}}},
        )

    @app.get("/unauth-token-expired")
    def unauth_token_expired() -> None:
        # An expired ID token is an ordinary "client fell behind on refresh"
        # state, not an attack — it must NOT increment the counter.
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "TOKEN_EXPIRED", "message": "...", "details": {}}},
        )

    @app.get("/unauth-idle-timeout")
    def unauth_idle_timeout() -> None:
        # The idle activity heartbeat lapsed; the session aged out. Expected
        # user state, not a brute-force signal — it must NOT increment.
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "IDLE_TIMEOUT", "message": "...", "details": {}}},
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
        # still emit, with reason=UNKNOWN — but only when the caller
        # presented something. See the NO_CREDENTIALS tests.
        raise HTTPException(status_code=401, detail="who?")

    @app.get("/forbidden-feature-not-enabled")
    def forbidden_feature_not_enabled() -> None:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "FEATURE_NOT_ENABLED", "message": "...", "details": {}}},
        )

    @app.get("/boom")
    def boom() -> None:
        # An unexpected, non-API exception (e.g. a DB driver error). It
        # must be caught by the catch-all handler, logged as one record,
        # and rendered as a generic 500 envelope.
        #
        # Deliberately SYNC: FastAPI runs a sync route in a threadpool, which
        # does not inherit the request's context. That is the case where the
        # route_template contextvar reads empty (see the async twin below).
        raise RuntimeError("kaboom")

    @app.get("/boom-async")
    async def boom_async() -> None:
        raise RuntimeError("kaboom")

    @app.get("/patients/{patient_id}/boom")
    def boom_with_id(patient_id: str) -> None:
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


def test_unhandled_log_carries_the_route_from_a_sync_endpoint(
    unhandled_logs: io.StringIO,
) -> None:
    """The traceback line is the most useful thing we emit, and it could not be
    attributed to an endpoint.

    A sync route runs in a threadpool, which does not inherit the request's
    context, so the route_template contextvar the formatter reads is empty —
    every unhandled exception raised under run_sync_in_worker_thread logged
    with no route at all.
    """
    client = TestClient(_build_app(), raise_server_exceptions=False)
    client.get("/boom")

    (record,) = _lines(unhandled_logs)
    assert record["route_template"] == "/boom"


def test_unhandled_log_carries_the_route_from_an_async_endpoint(
    unhandled_logs: io.StringIO,
) -> None:
    client = TestClient(_build_app(), raise_server_exceptions=False)
    client.get("/boom-async")

    (record,) = _lines(unhandled_logs)
    assert record["route_template"] == "/boom-async"


def test_unhandled_log_carries_the_template_never_the_populated_path(
    unhandled_logs: io.StringIO,
) -> None:
    """This record lands in a log stream read outside the request. The concrete
    path carries record ids; only the pattern may be logged."""
    client = TestClient(_build_app(), raise_server_exceptions=False)
    client.get("/patients/8f14e45f-ceea-467a-9f6e-4d2c1a3b5555/boom")

    (record,) = _lines(unhandled_logs)
    assert record["route_template"] == "/patients/{patient_id}/boom"
    assert "8f14e45f" not in json.dumps(record)


def _lines(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(ln) for ln in buf.getvalue().splitlines() if ln.strip()]


def test_emits_on_401_with_envelope_reason(auth_logs: io.StringIO) -> None:
    response = TestClient(_build_app()).get("/unauth-invalid-token")
    assert response.status_code == 401
    # Default FastAPI envelope shape must be preserved.
    assert response.json() == {
        "detail": {"error": {"code": "INVALID_TOKEN", "message": "...", "details": {}}}
    }
    payloads = _lines(auth_logs)
    assert len(payloads) == 1
    assert payloads[0]["event"] == "auth_failed"
    assert payloads[0]["reason"] == "INVALID_TOKEN"
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


def test_skips_idle_timeout(auth_logs: io.StringIO) -> None:
    # A session that aged out is expected-user, not a brute-force signal:
    # one dashboard load can fan out enough parallel 401s to trip the
    # per-IP spike alert on its own, so IDLE_TIMEOUT must not be counted.
    response = TestClient(_build_app()).get("/unauth-idle-timeout")
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "IDLE_TIMEOUT"
    assert _lines(auth_logs) == []


def test_skips_token_expired(auth_logs: io.StringIO) -> None:
    # An expired ID token carries no attack value and bursts the same way
    # on a single load; it must not increment the counter either.
    response = TestClient(_build_app()).get("/unauth-token-expired")
    assert response.status_code == 401
    assert response.json()["detail"]["error"]["code"] == "TOKEN_EXPIRED"
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
    """A credential WAS presented and rejected, so this is real signal."""
    TestClient(_build_app()).get(
        "/unauth-string-detail", headers={"Authorization": "Bearer something"}
    )
    payloads = _lines(auth_logs)
    assert len(payloads) == 1
    assert payloads[0]["reason"] == "UNKNOWN"


def test_skips_a_request_that_presented_no_credential(auth_logs: io.StringIO) -> None:
    """Nothing presented, nothing rejected — not an attack, and not counted.

    A browser opening the app asks whether it has a session before it can know,
    and the answer is a 401. Counting that as a credential attack counted the
    front door for being a door: on the dev deployment it was the single
    largest contributor to the auth-failure spike alert, which fired on our own
    e2e suite (THERAPY-8uww).
    """
    response = TestClient(_build_app()).get("/unauth-string-detail")
    assert response.status_code == 401, "the caller is still rejected"
    assert _lines(auth_logs) == [], "but it is not counted as attack signal"


def test_an_entitlement_denial_is_not_a_credential_attack(auth_logs: io.StringIO) -> None:
    """The caller proved who they are and was told the feature is not theirs.

    That is a gate working, not a credential being guessed, so it must not feed
    an alert whose whole job is to spot credentials being guessed.
    """
    response = TestClient(_build_app()).get(
        "/forbidden-feature-not-enabled", headers={"Authorization": "Bearer good-token"}
    )
    assert response.status_code == 403
    assert _lines(auth_logs) == []


def test_a_bad_token_still_counts_even_with_no_envelope(auth_logs: io.StringIO) -> None:
    """The exemption keys off ABSENCE, never off the reason being unreadable.

    If an unparseable detail were exempted wholesale, an attacker spraying
    tokens at any endpoint that raises a plain string would go uncounted.
    """
    TestClient(_build_app()).get("/unauth-string-detail", headers={"Authorization": "Bearer wrong"})
    payloads = _lines(auth_logs)
    assert len(payloads) == 1
    assert payloads[0]["event"] == "auth_failed"


def test_source_ip_recorded(auth_logs: io.StringIO) -> None:
    # TestClient sets the client address to "testclient" (127.0.0.1) so
    # we just verify the field is populated, not its exact value.
    TestClient(_build_app()).get("/unauth-invalid-token")
    payloads = _lines(auth_logs)
    assert "source_ip" in payloads[0]


def test_x_forwarded_for_used_when_present(auth_logs: io.StringIO) -> None:
    # The trusted proxy (Cloud Run) appends the real client IP on the RIGHT;
    # a client-supplied leftmost value is spoofable and must be ignored.
    TestClient(_build_app()).get(
        "/unauth-invalid-token",
        headers={"X-Forwarded-For": "1.2.3.4, 203.0.113.7"},
    )
    payloads = _lines(auth_logs)
    assert payloads[0]["source_ip"] == "203.0.113.7"
