# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Background (timer-driven) requests must not keep an idle session alive.

A visible tab polls the API on a timer whether or not anyone is at the
keyboard. Those polls carry ``X-Pablo-Background: 1``; they are checked
against the idle window but do not extend it. These tests run the idle
state machine against an in-memory Redis with a clock we move by hand, so
"the window lapses" is something that actually happens rather than a mocked
return value.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.auth.idle_session import check_and_touch
from app.auth.service import (
    enforce_idle_session,
    get_current_user_no_mfa,
    get_session_peek_claims,
    require_mfa,
)
from app.main import app
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.testclient import TestClient

_TOKEN: dict[str, Any] = {"uid": "user-123", "auth_time": 1_700_000_000}
_ACTIVITY_KEY = "idle:activity:user-123:1700000000"
_MARKER_KEY = "idle:session:user-123:1700000000"
_IDLE_SECONDS = 900
_BACKGROUND = {"X-Pablo-Background": "1"}


class _ClockedRedis:
    """Just enough of redis-py for the idle state machine, with a hand clock."""

    def __init__(self) -> None:
        self.now = 0.0
        self._expiry: dict[str, float] = {}

    def _live(self, key: str) -> bool:
        expires = self._expiry.get(key)
        if expires is None:
            return False
        if expires <= self.now:
            del self._expiry[key]
            return False
        return True

    def exists(self, key: str) -> int:
        return int(self._live(key))

    def set(self, key: str, _value: str, ex: int) -> None:
        self._expiry[key] = self.now + ex

    def delete(self, key: str) -> None:
        self._expiry.pop(key, None)

    def ttl(self, key: str) -> int:
        return int(self._expiry[key] - self.now) if self._live(key) else -2

    def pipeline(self) -> "_ClockedRedis":
        return self

    def execute(self) -> None:
        return None


@pytest.fixture
def redis() -> Iterator[_ClockedRedis]:
    fake = _ClockedRedis()
    settings = MagicMock()
    settings.is_development = False
    settings.idle_timeout_seconds = _IDLE_SECONDS
    settings.idle_session_fail_open = False
    with (
        patch("app.auth.idle_session.get_redis_client", return_value=fake),
        patch("app.auth.idle_session.get_settings", return_value=settings),
    ):
        yield fake


def _assert_idle_timeout(exc: pytest.ExceptionInfo[HTTPException]) -> None:
    assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert exc.value.detail["error"]["code"] == "IDLE_TIMEOUT"  # type: ignore[index]


class TestRefreshFlag:
    @pytest.mark.parametrize("refresh", [True, False])
    def test_first_request_after_sign_in_seeds_the_session(
        self, redis: _ClockedRedis, refresh: bool
    ) -> None:
        check_and_touch(_TOKEN, refresh=refresh)

        assert redis.exists(_MARKER_KEY)
        assert redis.ttl(_ACTIVITY_KEY) == _IDLE_SECONDS

    def test_background_check_does_not_extend_the_window(self, redis: _ClockedRedis) -> None:
        check_and_touch(_TOKEN)
        redis.now = 600

        check_and_touch(_TOKEN, refresh=False)

        assert redis.ttl(_ACTIVITY_KEY) == _IDLE_SECONDS - 600

    def test_background_check_is_rejected_once_the_window_lapses(
        self, redis: _ClockedRedis
    ) -> None:
        check_and_touch(_TOKEN)
        for minute in range(1, 15):
            redis.now = minute * 60
            check_and_touch(_TOKEN, refresh=False)

        redis.now = _IDLE_SECONDS
        with pytest.raises(HTTPException) as exc:
            check_and_touch(_TOKEN, refresh=False)
        _assert_idle_timeout(exc)

        # Terminal: a later foreground request cannot revive it either.
        with pytest.raises(HTTPException) as exc:
            check_and_touch(_TOKEN)
        _assert_idle_timeout(exc)

    def test_foreground_check_still_extends_the_window(self, redis: _ClockedRedis) -> None:
        check_and_touch(_TOKEN)
        redis.now = 600

        check_and_touch(_TOKEN)

        assert redis.ttl(_ACTIVITY_KEY) == _IDLE_SECONDS
        redis.now = 1400
        check_and_touch(_TOKEN)


def _guarded_app() -> FastAPI:
    """A route behind the real idle dependency, with token verification stubbed."""
    guarded = FastAPI()

    @guarded.get("/guarded")
    def guarded_route(_token: dict[str, Any] = Depends(enforce_idle_session)) -> dict[str, str]:
        return {"ok": "yes"}

    guarded.dependency_overrides[require_mfa] = lambda: _TOKEN
    return guarded


class TestRouteDependencyHonoursHeader:
    def test_polling_with_the_header_lets_the_session_idle_out(self, redis: _ClockedRedis) -> None:
        client = TestClient(_guarded_app())
        assert client.get("/guarded").status_code == 200

        for minute in range(1, 15):
            redis.now = minute * 60
            assert client.get("/guarded", headers=_BACKGROUND).status_code == 200

        redis.now = _IDLE_SECONDS
        response = client.get("/guarded", headers=_BACKGROUND)
        assert response.status_code == 401
        assert response.json()["detail"]["error"]["code"] == "IDLE_TIMEOUT"

    def test_polling_without_the_header_keeps_the_session_alive(self, redis: _ClockedRedis) -> None:
        client = TestClient(_guarded_app())
        for minute in range(0, 30):
            redis.now = minute * 60
            assert client.get("/guarded").status_code == 200

    @pytest.mark.parametrize("value", ["0", "true", ""])
    def test_only_the_value_1_marks_a_request_as_background(
        self, redis: _ClockedRedis, value: str
    ) -> None:
        client = TestClient(_guarded_app())
        client.get("/guarded")
        redis.now = 600

        client.get("/guarded", headers={"X-Pablo-Background": value})

        assert redis.ttl(_ACTIVITY_KEY) == _IDLE_SECONDS


class TestNoMfaPathHonoursHeader:
    def test_background_request_does_not_refresh(self, redis: _ClockedRedis) -> None:
        check_and_touch(_TOKEN)
        redis.now = 600
        request = MagicMock()
        request.headers = _BACKGROUND
        request.state.verified_firebase_token_raw = None

        with (
            patch("app.auth.service.verify_firebase_token", return_value=_TOKEN),
            patch("app.auth.service._resolve_user"),
        ):
            get_current_user_no_mfa(
                request, MagicMock(credentials="tok"), MagicMock(), MagicMock(), MagicMock()
            )

        assert redis.ttl(_ACTIVITY_KEY) == _IDLE_SECONDS - 600


class TestTouchEndpointAlwaysRefreshes:
    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        app.dependency_overrides[get_session_peek_claims] = lambda: _TOKEN
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.pop(get_session_peek_claims, None)

    @pytest.mark.parametrize("headers", [{}, _BACKGROUND])
    def test_touch_extends_the_window(
        self, redis: _ClockedRedis, client: TestClient, headers: dict[str, str]
    ) -> None:
        check_and_touch(_TOKEN)
        redis.now = 600

        response = client.post("/api/auth/session/touch", headers=headers)

        assert response.status_code == 200
        assert response.json()["seconds_remaining"] == _IDLE_SECONDS

    def test_peek_does_not_extend_the_window(
        self, redis: _ClockedRedis, client: TestClient
    ) -> None:
        check_and_touch(_TOKEN)
        redis.now = 600

        response = client.get("/api/auth/session")

        assert response.json()["seconds_remaining"] == _IDLE_SECONDS - 600
