# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""When ``record_sign_in`` writes, and when it must not.

The database half (the lookup, the lock, the row in the right practice) is
proven against Postgres in ``tests_integration/api/test_sign_in_audit.py``.
These pin the decisions around it with the database stood in for.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.auth import sign_in_audit
from app.auth.idle_session import session_log_key
from app.auth.service import get_current_user
from app.models import User
from app.models.audit import AuditAction
from app.repositories import (
    InMemoryAllowlistRepository,
    InMemoryIdentityRepository,
    InMemoryUserRepository,
)
from fastapi import HTTPException

if TYPE_CHECKING:
    from collections.abc import Iterator

_AUTH_TIME = 1_760_000_000
_CLAIMS: dict[str, Any] = {"uid": "fb-uid", "auth_time": _AUTH_TIME, "iat": _AUTH_TIME}


@pytest.fixture(autouse=True)
def _fresh_memo() -> Iterator[None]:
    sign_in_audit._memo.clear()
    yield
    sign_in_audit._memo.clear()


@contextmanager
def _database(
    *, schema: str | None = "practice_x", already_recorded: bool = False
) -> Iterator[MagicMock]:
    """Stand in for the request session, practice lookup and tenant session.

    Yields the audit write, so a test can count it.
    """

    @contextmanager
    def _tenant_session(_schema: str, _user_id: str) -> Iterator[MagicMock]:
        yield MagicMock()

    with (
        patch("app.auth.sign_in_audit.get_db_session", return_value=MagicMock()),
        patch(
            "app.auth.sign_in_audit.resolve_tenant_schema_for_user",
            return_value=schema,
        ),
        patch("app.auth.sign_in_audit.tenant_db_session", _tenant_session),
        patch(
            "app.repositories.postgres.audit.PostgresAuditRepository.lock_and_check_sign_in",
            return_value=already_recorded,
        ),
        patch("app.services.audit_service.AuditService.log_account_security_event") as write,
    ):
        yield write


def test_writes_one_row_carrying_the_sign_in_id() -> None:
    request = MagicMock()
    with _database() as write:
        sign_in_audit.record_sign_in("user-1", _CLAIMS, request)
    write.assert_called_once_with(
        AuditAction.SIGNED_IN, "user-1", request, changes={"sign_in": session_log_key(_CLAIMS)}
    )


def test_a_refreshed_token_is_the_same_sign_in() -> None:
    refreshed = {**_CLAIMS, "iat": _AUTH_TIME + 3000}
    with _database() as write:
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
        sign_in_audit.record_sign_in("user-1", refreshed, MagicMock())
    assert write.call_count == 1


def test_signing_in_again_is_a_new_row() -> None:
    again = {**_CLAIMS, "auth_time": _AUTH_TIME + 60}
    with _database() as write:
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
        sign_in_audit.record_sign_in("user-1", again, MagicMock())
    assert write.call_count == 2


def test_a_sign_in_the_log_already_holds_is_not_written_again() -> None:
    with _database(already_recorded=True) as write:
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
    write.assert_not_called()


def test_a_token_without_auth_time_is_skipped() -> None:
    with _database() as write:
        sign_in_audit.record_sign_in("user-1", {"uid": "fb-uid"}, MagicMock())
    write.assert_not_called()


def test_no_practice_writes_nothing_until_the_practice_exists() -> None:
    with _database(schema=None) as write:
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
    write.assert_not_called()
    with _database() as write:
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
    write.assert_called_once()


def test_a_failed_write_never_raises_and_is_tried_again() -> None:
    with _database() as write:
        write.side_effect = [RuntimeError("audit store down"), None]
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
    assert write.call_count == 2


def test_no_database_behind_the_request_is_a_no_op() -> None:
    def _no_session() -> Any:
        raise RuntimeError("No database session in context.")

    with (
        patch("app.auth.sign_in_audit.get_db_session", _no_session),
        patch("app.auth.sign_in_audit.resolve_tenant_schema_for_user") as resolve,
    ):
        sign_in_audit.record_sign_in("user-1", _CLAIMS, MagicMock())
    resolve.assert_not_called()


class TestTheWebAppsServerSessionIsNotASecondSignIn:
    """A sign-in through the web app was recorded twice.

    The browser signs in and presents its token to the web app's own server,
    which keeps a session of its own for pages it renders. That session's
    token is minted from the browser's (a custom-token exchange), so it has a
    ``custom`` provider, the browser's provider as ``source_sign_in_provider``,
    the browser's custom claims, and an ``auth_time`` of the exchange. It
    reaches the backend from the web app's server, with that server's address
    and agent. One sign-in, one row, written from the browser's own request.
    """

    _BROWSER: dict[str, Any] = {  # noqa: RUF012
        **_CLAIMS,
        "firebase": {"sign_in_provider": "password"},
    }

    @staticmethod
    def _server_copy(browser: dict[str, Any], *, minted_after: int = 1) -> dict[str, Any]:
        """The web app server's token for *browser*'s sign-in, as the exchange mints it."""
        minted = browser["auth_time"] + minted_after
        custom = {
            k: v
            for k, v in browser.items()
            if k not in {"uid", "auth_time", "iat", "firebase", "source_sign_in_provider"}
        }
        return {
            **custom,
            "uid": browser["uid"],
            "auth_time": minted,
            "iat": minted,
            "source_sign_in_provider": browser["firebase"]["sign_in_provider"],
            "firebase": {"sign_in_provider": "custom"},
        }

    def test_browser_then_server_is_one_row_from_the_browser(self) -> None:
        browser_request, server_request = MagicMock(), MagicMock()
        with _database() as write:
            sign_in_audit.record_sign_in("user-1", self._BROWSER, browser_request)
            sign_in_audit.record_sign_in("user-1", self._server_copy(self._BROWSER), server_request)
        write.assert_called_once()
        assert write.call_args.args[2] is browser_request

    def test_server_first_writes_nothing_and_the_browser_still_writes(self) -> None:
        browser_request = MagicMock()
        with _database() as write:
            sign_in_audit.record_sign_in("user-1", self._server_copy(self._BROWSER), MagicMock())
            write.assert_not_called()
            sign_in_audit.record_sign_in("user-1", self._BROWSER, browser_request)
        write.assert_called_once()
        assert write.call_args.args[2] is browser_request
        assert write.call_args.kwargs["changes"] == {"sign_in": session_log_key(self._BROWSER)}

    def test_each_new_server_session_for_the_same_sign_in_writes_nothing(self) -> None:
        """The web app re-syncs its session on every browser token change."""
        with _database() as write:
            sign_in_audit.record_sign_in("user-1", self._BROWSER, MagicMock())
            for minted_after in (1, 2, 3600, 7200):
                server = self._server_copy(self._BROWSER, minted_after=minted_after)
                sign_in_audit.record_sign_in("user-1", server, MagicMock())
        assert write.call_count == 1

    def test_a_google_sign_in_with_a_second_factor_is_one_row(self) -> None:
        browser = {
            **_CLAIMS,
            "firebase": {"sign_in_provider": "google.com", "sign_in_second_factor": "totp"},
        }
        with _database() as write:
            sign_in_audit.record_sign_in("user-1", self._server_copy(browser), MagicMock())
            sign_in_audit.record_sign_in("user-1", browser, MagicMock())
            sign_in_audit.record_sign_in("user-1", self._server_copy(browser), MagicMock())
        assert write.call_count == 1

    def test_a_passkey_sign_in_is_one_row(self) -> None:
        """The browser itself signs in with a custom token here, and is recorded."""
        browser = {
            **_CLAIMS,
            "pablo_amr": ["webauthn"],
            "firebase": {"sign_in_provider": "custom"},
        }
        server = self._server_copy(browser)
        assert server["pablo_amr"] == ["webauthn"]
        with _database() as write:
            sign_in_audit.record_sign_in("user-1", browser, MagicMock())
            sign_in_audit.record_sign_in("user-1", server, MagicMock())
        write.assert_called_once()
        assert write.call_args.kwargs["changes"] == {"sign_in": session_log_key(browser)}


class TestTheAuthSeamRecordsTheSignIn:
    """``_resolve_user`` is where every authenticated request lands."""

    @staticmethod
    def _resolve(status: str) -> tuple[MagicMock, Exception | None]:

        users = InMemoryUserRepository()
        users.update(
            User(
                id="user-1",
                email="user-1@example.com",
                name="User",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                status=status,
            )
        )
        identities = InMemoryIdentityRepository()
        identities.link("firebase", "fb-uid", "user-1")
        request = MagicMock()
        request.headers = {}
        raised: Exception | None = None
        with (
            patch("app.auth.service.get_settings") as settings,
            patch("app.auth.sign_in_audit.record_sign_in") as record,
        ):
            settings.return_value.restrict_signups = False
            try:
                get_current_user(
                    request, dict(_CLAIMS), users, InMemoryAllowlistRepository(), identities
                )
            except Exception as err:
                raised = err
        return record, raised

    def test_an_active_user_is_recorded(self) -> None:
        record, raised = self._resolve("approved")
        assert raised is None
        record.assert_called_once()
        assert record.call_args.args[:2] == ("user-1", _CLAIMS)

    def test_a_disabled_user_is_refused_before_anything_is_recorded(self) -> None:
        record, raised = self._resolve("disabled")
        assert isinstance(raised, HTTPException)
        assert raised.status_code == 403
        record.assert_not_called()
