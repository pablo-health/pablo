# Copyright (c) 2026 Pablo Health. AGPL-3.0.
"""Unit tests for ``assert_no_held_db_connection`` — the guard that keeps a
pooled DB connection from being held across a slow external call (e.g. an LLM
request). Fail-loud in dev/test; log-only in production.

The second half covers the case the guard used to miss entirely: a caller that
opens its own session and publishes it displaces the previous one, which stays
open with nothing pointing at it. Reading only ``_request_session`` could not
see that session — so the guard was blind in precisely the situation it exists
for, and the stranded connection was killed by the database's
idle-in-transaction timeout instead, surfacing as a teardown failure on a
request whose work had already succeeded.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from app.db import (
    _displaced_sessions,
    _request_session,
    assert_no_held_db_connection,
    bound_db_sessions,
    publish_request_session,
    release_db_connection,
    restore_request_session,
)


def _bind_session(*, in_transaction: bool) -> tuple[object, MagicMock]:
    session = MagicMock()
    session.in_transaction.return_value = in_transaction
    return _request_session.set(session), session


def test_noop_when_no_request_session() -> None:
    token = _request_session.set(None)
    try:
        assert_no_held_db_connection("structured-llm")  # must not raise
    finally:
        _request_session.reset(token)


def test_noop_when_session_has_no_open_transaction() -> None:
    # The released, correct state: connection returned to the pool.
    token, _ = _bind_session(in_transaction=False)
    try:
        assert_no_held_db_connection("structured-llm")  # must not raise
    finally:
        _request_session.reset(token)


def test_raises_in_dev_when_connection_held() -> None:
    token, _ = _bind_session(in_transaction=True)
    try:
        with patch("app.settings.get_settings") as get_settings:
            get_settings.return_value.is_production = False
            with pytest.raises(RuntimeError, match="release_db_connection"):
                assert_no_held_db_connection("structured-llm")
    finally:
        _request_session.reset(token)


def test_logs_but_does_not_raise_in_production(caplog: pytest.LogCaptureFixture) -> None:
    token, _ = _bind_session(in_transaction=True)
    try:
        with (
            patch("app.settings.get_settings") as get_settings,
            caplog.at_level(logging.ERROR),
        ):
            get_settings.return_value.is_production = True
            assert_no_held_db_connection("chat-llm")  # must not raise in prod
        assert "held_db_connection_during_external_call" in caplog.text
        assert "chat-llm" in caplog.text
    finally:
        _request_session.reset(token)


def _session(*, in_transaction: bool) -> MagicMock:
    """A session mock whose ``commit`` ends its transaction, as a real one does."""
    session = MagicMock()
    session.in_transaction.return_value = in_transaction

    def _commit() -> None:
        session.in_transaction.return_value = False

    session.commit.side_effect = _commit
    return session


def test_guard_sees_a_session_displaced_by_a_bare_repoint() -> None:
    """The original blind spot, reproduced with the ContextVar set directly.

    This is the shape that produced the incident: something re-points
    ``_request_session`` without going through ``publish_request_session``, so
    the displaced session is never released. The guard has to catch it — for a
    bare re-point it is the only thing that will.
    """
    stranded = _session(in_transaction=True)
    outer = _request_session.set(stranded)
    # Track it as displaced, the way a publication does, then point the
    # ContextVar at a session that is correctly released.
    displaced = _displaced_sessions.set((stranded,))
    inner = _request_session.set(_session(in_transaction=False))
    try:
        with patch("app.settings.get_settings") as get_settings:
            get_settings.return_value.is_production = False
            with pytest.raises(RuntimeError, match="release_db_connection"):
                assert_no_held_db_connection("gmail-fetch")
    finally:
        _request_session.reset(inner)
        _displaced_sessions.reset(displaced)
        _request_session.reset(outer)


def test_publishing_releases_the_session_it_displaces() -> None:
    """The fix itself: the displaced session's connection goes back to the pool.

    A guard alone cannot satisfy this — in production it only logs, so the
    connection would still be held. The release has to happen at the
    displacement.
    """
    middleware = _session(in_transaction=True)
    outer = _request_session.set(middleware)
    try:
        binding = publish_request_session(_session(in_transaction=True))
        try:
            middleware.commit.assert_called_once()
            assert middleware.in_transaction() is False
            # Still tracked, so a later guard call can still see it.
            assert middleware in bound_db_sessions()
        finally:
            restore_request_session(binding)
        # Restored: the displaced session is the published one again.
        assert bound_db_sessions() == (middleware,)
    finally:
        _request_session.reset(outer)


def test_publishing_over_nothing_displaces_nothing() -> None:
    # Off-request (CLI, cron, a worker thread with no bound session).
    token = _request_session.set(None)
    try:
        own = _session(in_transaction=True)
        binding = publish_request_session(own)
        try:
            assert bound_db_sessions() == (own,)
        finally:
            restore_request_session(binding)
    finally:
        _request_session.reset(token)


def test_release_releases_every_bound_session() -> None:
    stranded = _session(in_transaction=True)
    current = _session(in_transaction=True)
    displaced = _displaced_sessions.set((stranded,))
    token = _request_session.set(current)
    try:
        release_db_connection()
        stranded.commit.assert_called_once()
        current.commit.assert_called_once()
        with patch("app.settings.get_settings") as get_settings:
            get_settings.return_value.is_production = False
            assert_no_held_db_connection("after-release")  # must not raise
    finally:
        _request_session.reset(token)
        _displaced_sessions.reset(displaced)


def test_bound_sessions_is_empty_off_request() -> None:
    token = _request_session.set(None)
    try:
        assert bound_db_sessions() == ()
    finally:
        _request_session.reset(token)
