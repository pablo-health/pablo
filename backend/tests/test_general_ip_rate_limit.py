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
from app.middleware.ip_rate_limit import RATE_LIMITED_ERROR_CODE, WINDOW_SECONDS
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware import Middleware

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.settings import Settings


def _settings(per_min: int, exempt: str = "") -> Settings:
    return get_settings().model_copy(
        update={
            "general_ip_rate_limit_per_min": per_min,
            "general_ip_rate_limit_exempt_ips": exempt,
        }
    )


def _client(per_min: int, exempt: str = "", *, peer: str = "203.0.113.7") -> TestClient:
    small = FastAPI()

    @small.get("/api/things")
    def things() -> dict[str, str]:
        return {"ok": "yes"}

    @small.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "healthy"}

    small.add_middleware(GeneralIPRateLimitMiddleware, settings=_settings(per_min, exempt))
    return TestClient(small, client=(peer, 50000))


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
