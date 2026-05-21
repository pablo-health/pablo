# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the pool-checkout listener that re-applies search_path
from the request-scoped ContextVar (``app.db._reapply_search_path_on_checkout``).

The listener is a belt-and-braces companion to the explicit
``set_tenant_schema`` call middleware makes per request — useful when
a pooled connection's server-side ``search_path`` would otherwise
carry over from a previous tenant.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from app.db import (
    _current_tenant_schema,
    _reapply_search_path_on_checkout,
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
    cursor.execute.assert_called_once_with(
        "SET search_path = practice_abc123, platform, public"
    )
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
