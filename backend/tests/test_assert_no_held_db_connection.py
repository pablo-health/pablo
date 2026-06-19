# Copyright (c) 2026 Pablo Health. AGPL-3.0.
"""Unit tests for ``assert_no_held_db_connection`` — the guard that keeps a
pooled DB connection from being held across a slow external call (e.g. an LLM
request). Fail-loud in dev/test; log-only in production.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from app.db import _request_session, assert_no_held_db_connection


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
