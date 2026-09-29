# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the pool listeners around a connection's ``search_path``.

The checkout listener (``app.db._reapply_search_path_on_checkout``)
re-applies search_path from the request-scoped ContextVar — a
belt-and-braces companion to the explicit ``set_tenant_schema`` call
middleware makes per request, useful when a pooled connection's
server-side ``search_path`` would otherwise carry over from a previous
tenant.

The checkin listener (``app.db._reset_search_path_on_checkin``) sends the
connection back to the pool neutral: no tenant schema, and neither
principal setting armed.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.db import (
    _current_tenant_schema,
    _reapply_search_path_on_checkout,
    _reset_search_path_on_checkin,
)


@pytest.fixture(autouse=True)
def _reset_tenant_schema():
    """Ensure each test starts with the ContextVar cleared, regardless of
    what a prior test (or any module import) might have left in it."""
    token = _current_tenant_schema.set(None)
    yield
    _current_tenant_schema.reset(token)


def _fake_dbapi_conn() -> MagicMock:
    """Build a minimal dbapi conn mock — only cursor() is exercised by the
    listener."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    return conn


def test_no_op_when_contextvar_unset() -> None:
    conn = _fake_dbapi_conn()
    _reapply_search_path_on_checkout(conn, None, None)
    conn.cursor.assert_not_called()


def test_applies_search_path_when_contextvar_set() -> None:
    _current_tenant_schema.set("practice_abc123")
    conn = _fake_dbapi_conn()
    _reapply_search_path_on_checkout(conn, None, None)
    conn.cursor.assert_called_once()
    cursor = conn.cursor.return_value
    cursor.execute.assert_called_once_with("SET search_path = practice_abc123, platform, public")
    cursor.close.assert_called_once()


def test_refuses_to_apply_invalid_schema() -> None:
    """Defensive: even if a bad schema name reached the ContextVar (it
    shouldn't — ``set_tenant_schema`` validates first), refuse the SET
    rather than interpolate untrusted input into raw SQL."""
    _current_tenant_schema.set("practice; DROP TABLE foo--")
    conn = _fake_dbapi_conn()
    _reapply_search_path_on_checkout(conn, None, None)
    conn.cursor.assert_not_called()


def test_cursor_closed_even_if_execute_raises() -> None:
    _current_tenant_schema.set("practice_abc123")
    conn = _fake_dbapi_conn()
    conn.cursor.return_value.execute.side_effect = RuntimeError("simulated")
    with pytest.raises(RuntimeError):
        _reapply_search_path_on_checkout(conn, None, None)
    conn.cursor.return_value.close.assert_called_once()


# --- checkin: the connection goes back to the pool neutral ------------------


def test_checkin_neutralises_search_path_and_both_principals() -> None:
    """The next checkout gets no tenant and nobody to read as."""
    conn = _fake_dbapi_conn()
    conn.autocommit = False

    _reset_search_path_on_checkin(conn, None)

    executed = [call.args[0] for call in conn.cursor.return_value.execute.call_args_list]
    assert executed == [
        "SET search_path = platform, public",
        "RESET app.current_user_id",
        "RESET app.current_patient_id",
    ]
    conn.cursor.return_value.close.assert_called_once()


def test_checkin_runs_outside_a_transaction_and_restores_autocommit() -> None:
    """The resets run in autocommit, so none of them opens a transaction the
    next checkout's pre-ping would trip over; the prior mode comes back."""
    conn = _fake_dbapi_conn()
    conn.autocommit = False
    seen: list[bool] = []
    conn.cursor.return_value.execute.side_effect = lambda _sql: seen.append(conn.autocommit)

    _reset_search_path_on_checkin(conn, None)

    assert seen == [True, True, True]
    assert conn.autocommit is False


def test_checkin_restores_autocommit_even_if_a_reset_raises() -> None:
    conn = _fake_dbapi_conn()
    conn.autocommit = False
    conn.cursor.return_value.execute.side_effect = RuntimeError("simulated")

    with pytest.raises(RuntimeError):
        _reset_search_path_on_checkin(conn, None)

    conn.cursor.return_value.close.assert_called_once()
    assert conn.autocommit is False


def test_checkin_of_an_invalidated_connection_is_a_no_op() -> None:
    _reset_search_path_on_checkin(None, None)
