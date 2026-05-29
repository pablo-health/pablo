# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the server-side idle session enforcement.

Mocks the Redis client; we're verifying the state machine
(missing-marker / active / expired-activity), not Redis itself.
"""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.auth.idle_session import check_and_touch
from app.auth.service import enforce_idle_session, get_current_user_no_mfa
from fastapi import HTTPException, status

_TOKEN: dict[str, Any] = {"uid": "user-123", "auth_time": 1_700_000_000}
_MARKER_KEY = "idle:session:user-123:1700000000"
_ACTIVITY_KEY = "idle:activity:user-123:1700000000"
_REVOKED_KEY = "idle:revoked:user-123:1700000000"
_MARKER_TTL = 31 * 24 * 60 * 60


def _patch_deps(
    redis: MagicMock | None,
    *,
    is_development: bool = False,
    fail_open: bool = False,
):
    """Patch get_redis_client + get_settings together."""
    settings = MagicMock()
    settings.is_development = is_development
    settings.idle_timeout_seconds = 900
    settings.idle_session_fail_open = fail_open
    return (
        patch("app.auth.idle_session.get_redis_client", return_value=redis),
        patch("app.auth.idle_session.get_settings", return_value=settings),
    )


class TestCheckAndTouch:
    def test_dev_mode_is_skipped(self) -> None:
        redis = MagicMock()
        rc_patch, set_patch = _patch_deps(redis, is_development=True)
        with rc_patch, set_patch:
            check_and_touch(_TOKEN)
        redis.exists.assert_not_called()

    def test_redis_unavailable_is_skipped(self) -> None:
        rc_patch, set_patch = _patch_deps(None)
        with rc_patch, set_patch:
            check_and_touch(_TOKEN)  # no raise

    def test_first_request_seeds_both_keys(self) -> None:
        redis = MagicMock()
        redis.exists.return_value = 0  # revoked absent, marker missing
        pipe = MagicMock()
        redis.pipeline.return_value = pipe

        rc_patch, set_patch = _patch_deps(redis)
        with rc_patch, set_patch:
            check_and_touch(_TOKEN)

        assert redis.exists.call_args_list[0].args == (_REVOKED_KEY,)
        pipe.set.assert_any_call(_MARKER_KEY, "1", ex=_MARKER_TTL)
        pipe.set.assert_any_call(_ACTIVITY_KEY, "1", ex=900)
        pipe.execute.assert_called_once()

    def test_active_session_refreshes_activity(self) -> None:
        redis = MagicMock()
        # revoked absent, marker present, activity present.
        redis.exists.side_effect = [0, 1, 1]

        rc_patch, set_patch = _patch_deps(redis)
        with rc_patch, set_patch:
            check_and_touch(_TOKEN)

        redis.set.assert_called_once_with(_ACTIVITY_KEY, "1", ex=900)
        redis.delete.assert_not_called()
        redis.pipeline.assert_not_called()

    def test_idle_expiry_burns_marker_and_tombstones(self) -> None:
        redis = MagicMock()
        # revoked absent, marker present, activity expired.
        redis.exists.side_effect = [0, 1, 0]
        pipe = MagicMock()
        redis.pipeline.return_value = pipe

        rc_patch, set_patch = _patch_deps(redis)
        with rc_patch, set_patch, pytest.raises(HTTPException) as exc:
            check_and_touch(_TOKEN)

        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.value.detail["error"]["code"] == "IDLE_TIMEOUT"  # type: ignore[index]
        pipe.delete.assert_called_once_with(_MARKER_KEY)
        pipe.set.assert_called_once_with(_REVOKED_KEY, "1", ex=_MARKER_TTL)
        pipe.execute.assert_called_once()

    def test_revoked_session_is_rejected_without_rearming(self) -> None:
        """Bug B regression: a tombstoned auth_time can't revive itself."""
        redis = MagicMock()
        redis.exists.return_value = 1  # revoked key present

        rc_patch, set_patch = _patch_deps(redis)
        with rc_patch, set_patch, pytest.raises(HTTPException) as exc:
            check_and_touch(_TOKEN)

        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.value.detail["error"]["code"] == "IDLE_TIMEOUT"  # type: ignore[index]
        redis.exists.assert_called_once_with(_REVOKED_KEY)
        redis.set.assert_not_called()
        redis.delete.assert_not_called()
        redis.pipeline.assert_not_called()

    def test_missing_uid_or_auth_time_rejects(self) -> None:
        redis = MagicMock()
        rc_patch, set_patch = _patch_deps(redis)
        for bad_token in ({}, {"uid": "x"}, {"auth_time": 123}):
            with rc_patch, set_patch, pytest.raises(HTTPException) as exc:
                check_and_touch(bad_token)
            assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
            assert exc.value.detail["error"]["code"] == "INVALID_TOKEN"  # type: ignore[index]

    def test_redis_failure_fails_closed_by_default(self) -> None:
        """A Redis error must not silently disable the auto-logoff control.

        Default is fail-closed: reject with 503 (retryable) rather than
        allowing the request through with no idle enforcement.
        """
        redis = MagicMock()
        redis.exists.side_effect = RuntimeError("redis went away")

        rc_patch, set_patch = _patch_deps(redis)
        with rc_patch, set_patch, pytest.raises(HTTPException) as exc:
            check_and_touch(_TOKEN)
        assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert exc.value.detail["error"]["code"] == "IDLE_CHECK_UNAVAILABLE"  # type: ignore[index]

    def test_redis_failure_allows_when_fail_open_enabled(self) -> None:
        """Opt-in availability mode: a Redis error lets the request through."""
        redis = MagicMock()
        redis.exists.side_effect = RuntimeError("redis went away")

        rc_patch, set_patch = _patch_deps(redis, fail_open=True)
        with rc_patch, set_patch:
            check_and_touch(_TOKEN)  # no raise


class TestNoMfaPathEnforcesIdle:
    """Bug A regression: the no-MFA auth dep must still run the idle gate."""

    def test_no_mfa_path_rejects_idle_session(self) -> None:
        redis = MagicMock()
        # revoked absent, marker present, activity expired → idle timeout.
        redis.exists.side_effect = [0, 1, 0]
        redis.pipeline.return_value = MagicMock()

        request = MagicMock()
        request.state.verified_firebase_token_raw = None
        creds = MagicMock(credentials="tok")
        resolve = MagicMock()

        rc_patch, set_patch = _patch_deps(redis)
        with (
            rc_patch,
            set_patch,
            patch("app.auth.service.verify_firebase_token", return_value=_TOKEN),
            patch("app.auth.service._resolve_user", resolve),
            pytest.raises(HTTPException) as exc,
        ):
            get_current_user_no_mfa(request, creds, MagicMock(), MagicMock(), MagicMock())

        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.value.detail["error"]["code"] == "IDLE_TIMEOUT"  # type: ignore[index]
        resolve.assert_not_called()


class TestEnforceIdleSessionWrapper:
    """The Depends-friendly wrapper in service.py just chains and returns."""

    def test_returns_decoded_token_on_success(self) -> None:
        with patch("app.auth.idle_session.check_and_touch") as mock_check:
            result = enforce_idle_session(_TOKEN)
        assert result is _TOKEN
        mock_check.assert_called_once_with(_TOKEN)

    def test_propagates_idle_timeout_exception(self) -> None:
        boom = HTTPException(status_code=401, detail={"error": {"code": "IDLE_TIMEOUT"}})
        with (
            patch("app.auth.idle_session.check_and_touch", side_effect=boom),
            pytest.raises(HTTPException) as exc,
        ):
            enforce_idle_session(_TOKEN)
        assert exc.value is boom
