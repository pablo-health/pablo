# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The general per-address request ceiling (``GeneralIPRateLimitMiddleware``).

Two halves:

* the middleware on its own, in front of a two-route app: the limit, the
  answer it gives, what it never limits, and what happens when the limiter
  cannot answer;
* the middleware where ``app.main`` registers it, with the real stack in its
  real order: a refused request opens no database session.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.db import middleware as db_middleware
from app.main import app
from app.middleware import GeneralIPRateLimitMiddleware
from app.middleware.ip_rate_limit import (
    RATE_LIMITED_ERROR_CODE,
    UNCHECKED_WARNING_INTERVAL_SECONDS,
    WINDOW_SECONDS,
)
from app.request_context import (
    FORWARD_AUTH_HEADER,
    FORWARDED_CLIENT_IP_HEADER,
)
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.middleware import Middleware

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.settings import Settings


#: Caddy's address on the VM, and the web app server's behind it.
CADDY = "172.30.0.10"
WEB_APP_SERVER = "172.30.0.20"
FORWARD_SECRET = "s3cret-forward"
WRONG_SECRET = "guessed"


def _settings(per_min: int, exempt: str = "", **extra: Any) -> Settings:
    return get_settings().model_copy(
        update={
            "general_ip_rate_limit_per_min": per_min,
            "general_ip_rate_limit_exempt_ips": exempt,
            **extra,
        }
    )


def _client(
    per_min: int, exempt: str = "", *, peer: str = "203.0.113.7", **extra: Any
) -> TestClient:
    small = FastAPI()

    @small.get("/api/things")
    def things() -> dict[str, str]:
        return {"ok": "yes"}

    @small.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    small.add_middleware(GeneralIPRateLimitMiddleware, settings=_settings(per_min, exempt, **extra))
    return TestClient(small, client=(peer, 50000))


def _behind_caddy(per_min: int) -> TestClient:
    """A backend behind Caddy, with the web app server's forward secret set."""
    return _client(
        per_min,
        peer=CADDY,
        trusted_proxy_ips=CADDY,
        frontend_forward_secret=SecretStr(FORWARD_SECRET),
    )


def _from_web_app_server(visitor: str | None, secret: str = FORWARD_SECRET) -> dict[str, str]:
    """Headers on a call the web app's server makes through Caddy for ``visitor``."""
    headers = {"X-Forwarded-For": WEB_APP_SERVER, FORWARD_AUTH_HEADER: secret}
    if visitor is not None:
        headers[FORWARDED_CLIENT_IP_HEADER] = visitor
    return headers


class TestCeiling:
    def test_requests_under_the_limit_pass(self) -> None:
        client = _client(3)
        for _ in range(3):
            assert client.get("/api/things").status_code == 200

    def test_the_request_over_the_limit_is_refused_with_retry_after(self) -> None:
        client = _client(3)
        for _ in range(3):
            client.get("/api/things")

        response = client.get("/api/things")

        assert response.status_code == 429
        assert response.headers["Retry-After"] == str(WINDOW_SECONDS)
        assert response.json() == {"detail": "Too many requests. Please try again later."}

    def test_each_address_has_its_own_budget(self) -> None:
        settings = _settings(1)
        small = FastAPI()

        @small.get("/api/things")
        def things() -> dict[str, str]:
            return {"ok": "yes"}

        small.add_middleware(GeneralIPRateLimitMiddleware, settings=settings)
        first = TestClient(small, client=("203.0.113.7", 50000))
        second = TestClient(small, client=("198.51.100.4", 50000))

        assert first.get("/api/things").status_code == 200
        assert first.get("/api/things").status_code == 429
        assert second.get("/api/things").status_code == 200

    def test_a_forwarded_header_from_an_untrusted_peer_buys_no_fresh_budget(self) -> None:
        # The default trusts no proxy, so the header is the caller's own words.
        client = _client(1)
        assert client.get("/api/things", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200
        response = client.get("/api/things", headers={"X-Forwarded-For": "2.2.2.2"})
        assert response.status_code == 429

    def test_behind_a_trusted_proxy_each_forwarded_client_has_its_own_budget(self) -> None:
        # Behind Caddy every request's peer is Caddy. Keyed on the peer, the
        # whole deployment would share one budget.
        client = _client(1, peer=CADDY, trusted_proxy_ips=CADDY)
        first = {"X-Forwarded-For": "1.1.1.1"}
        second = {"X-Forwarded-For": "2.2.2.2"}

        assert client.get("/api/things", headers=first).status_code == 200
        assert client.get("/api/things", headers=second).status_code == 200
        assert client.get("/api/things", headers=first).status_code == 429
        assert client.get("/api/things", headers=second).status_code == 429

    def test_refusal_is_logged_without_the_path(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.WARNING, logger="app.middleware.ip_rate_limit")
        client = _client(1)
        client.get("/api/things")
        client.get("/api/things")

        records = [
            r for r in caplog.records if getattr(r, "event", None) == "general_ip_rate_limited"
        ]
        assert len(records) == 1
        line = records[0].getMessage()
        assert "/api/things" not in line
        assert "203.0.113.7" not in line


class TestWebAppServer:
    """Calls the web app's server makes for visitors: a practice website's files,
    host lookups, server-rendered pages. All of them reach the backend from one
    address, the server's."""

    def test_each_visitor_it_forwards_has_their_own_budget(self) -> None:
        client = _behind_caddy(1)
        visitors = ["198.51.100.1", "198.51.100.2", "198.51.100.3"]

        for visitor in visitors:
            response = client.get("/api/things", headers=_from_web_app_server(visitor))
            assert response.status_code == 200
        # The ceiling still holds per visitor.
        again = client.get("/api/things", headers=_from_web_app_server(visitors[0]))
        assert again.status_code == 429

    def test_a_visitor_is_one_budget_whether_forwarded_or_direct(self) -> None:
        client = _behind_caddy(1)
        visitor = "198.51.100.9"
        assert client.get("/api/things", headers=_from_web_app_server(visitor)).status_code == 200
        direct = client.get("/api/things", headers={"X-Forwarded-For": visitor})
        assert direct.status_code == 429

    def test_its_own_calls_naming_no_visitor_are_not_counted(self) -> None:
        # Cached host lookups are shared by every visitor, so they name none.
        client = _behind_caddy(1)
        for _ in range(5):
            response = client.get("/api/things", headers=_from_web_app_server(None))
            assert response.status_code == 200
        # The server's own address still has its whole budget for anything else.
        plain = {"X-Forwarded-For": WEB_APP_SERVER}
        assert client.get("/api/things", headers=plain).status_code == 200
        assert client.get("/api/things", headers=plain).status_code == 429

    def test_a_wrong_secret_buys_no_fresh_budget(self) -> None:
        client = _behind_caddy(1)
        forged = [
            _from_web_app_server(visitor, WRONG_SECRET)
            for visitor in ("198.51.100.1", "198.51.100.2")
        ]
        assert client.get("/api/things", headers=forged[0]).status_code == 200
        assert client.get("/api/things", headers=forged[1]).status_code == 429

    def test_a_wrong_secret_naming_no_visitor_is_counted(self) -> None:
        client = _behind_caddy(1)
        forged = _from_web_app_server(None, WRONG_SECRET)
        assert client.get("/api/things", headers=forged).status_code == 200
        assert client.get("/api/things", headers=forged).status_code == 429

    def test_with_no_secret_configured_the_headers_buy_nothing(self) -> None:
        client = _client(1, peer=CADDY, trusted_proxy_ips=CADDY)
        assert client.get("/api/things", headers=_from_web_app_server(None)).status_code == 200
        second = client.get("/api/things", headers=_from_web_app_server("198.51.100.1"))
        assert second.status_code == 429


class TestNeverLimited:
    def test_default_zero_changes_nothing(self) -> None:
        assert get_settings().general_ip_rate_limit_per_min == 0
        with patch("app.rate_limit._create_limiter") as create:
            client = _client(get_settings().general_ip_rate_limit_per_min)
            for _ in range(50):
                assert client.get("/api/things").status_code == 200
        # Off means off: no limiter is built and nothing is counted.
        create.assert_not_called()

    def test_health_check_is_never_limited(self) -> None:
        client = _client(1)
        client.get("/api/things")
        assert client.get("/api/things").status_code == 429
        for _ in range(5):
            assert client.get("/api/health").status_code == 200

    def test_exempt_address_is_never_limited(self) -> None:
        client = _client(1, exempt="10.0.0.5, 203.0.113.7")
        for _ in range(5):
            assert client.get("/api/things").status_code == 200

    def test_a_limiter_that_cannot_answer_lets_the_request_through(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broken = MagicMock()
        broken.check.side_effect = ConnectionError("redis unreachable")
        caplog.set_level(logging.WARNING, logger="app.middleware.ip_rate_limit")
        with patch("app.rate_limit._create_limiter", return_value=broken):
            client = _client(1)
            assert client.get("/api/things").status_code == 200
            assert client.get("/api/things").status_code == 200
        assert broken.check.call_count == 2
        assert any("could not be checked" in r.getMessage() for r in caplog.records)

    def test_an_outage_is_logged_once_per_interval_not_per_request(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broken = MagicMock()
        broken.check.side_effect = ConnectionError("redis unreachable")
        caplog.set_level(logging.WARNING, logger="app.middleware.ip_rate_limit")
        clock = [1000.0]

        def unchecked() -> int:
            return sum("could not be checked" in r.getMessage() for r in caplog.records)

        with (
            patch("app.rate_limit._create_limiter", return_value=broken),
            patch("app.middleware.ip_rate_limit.time.monotonic", side_effect=lambda: clock[0]),
        ):
            client = _client(1)
            for _ in range(5):
                assert client.get("/api/things").status_code == 200
            assert unchecked() == 1

            clock[0] += UNCHECKED_WARNING_INTERVAL_SECONDS
            assert client.get("/api/things").status_code == 200
        assert unchecked() == 2


@pytest.fixture
def real_app_limit() -> Iterator[None]:
    """Turn the ceiling on in ``app.main``'s own middleware stack, then restore it.

    The entry is swapped in place, so the stack keeps the order ``app.main``
    gave it; Starlette builds the stack lazily, so dropping the built one makes
    the next request rebuild it from the edited list.
    """
    original = list(app.user_middleware)
    classes: list[object] = [m.cls for m in original]
    index = classes.index(GeneralIPRateLimitMiddleware)
    app.user_middleware[index] = Middleware(GeneralIPRateLimitMiddleware, settings=_settings(2))
    app.middleware_stack = None
    try:
        yield
    finally:
        app.user_middleware[:] = original
        app.middleware_stack = None


def test_registered_outside_the_database_session_middleware() -> None:
    # user_middleware lists the outermost layer first.
    classes: list[object] = [m.cls for m in app.user_middleware]
    assert classes.index(GeneralIPRateLimitMiddleware) < classes.index(
        db_middleware.DatabaseSessionMiddleware
    )


@pytest.mark.usefixtures("real_app_limit")
def test_a_refused_request_opens_no_database_session(client: Any) -> None:
    spy = MagicMock(wraps=db_middleware.get_session_factory)
    with patch("app.db.middleware.get_session_factory", spy):
        assert client.get("/api/patients").status_code == 200
        assert client.get("/api/patients").status_code == 200
        # The control: each request that got through opened a session.
        assert spy.call_count == 2

        refused = client.get("/api/patients")

    assert refused.status_code == 429
    assert spy.call_count == 2


@pytest.mark.usefixtures("real_app_limit")
def test_a_refused_request_carries_the_error_code_on_its_access_line(
    client: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pablo.access")
    for _ in range(3):
        client.get("/api/patients")

    refused = [
        r
        for r in caplog.records
        if getattr(r, "event", None) == "request_completed" and getattr(r, "status_code", 0) == 429
    ]
    assert len(refused) == 1
    assert getattr(refused[0], "error_code", None) == RATE_LIMITED_ERROR_CODE
