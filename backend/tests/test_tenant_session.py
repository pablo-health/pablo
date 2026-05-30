# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for app.db.tenant_session primitives.

These tests use mocked DB machinery so they run without a live Postgres
instance (same pattern as the existing test_db_search_path_listener tests).

Coverage targets:
* tenant_db_session: ContextVar setup/teardown, commit on clean exit,
  rollback on exception, fail-closed guard, nested usage, and re-arming
  of the RLS GUC on the first transaction.
* run_in_tenant: dispatches to a worker thread, passes session as first
  arg, propagates return value and exceptions.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest
from app.db import _current_tenant_schema, _current_user_id, _request_session
from app.db.tenant_session import run_in_tenant, tenant_db_session

# ---------------------------------------------------------------------------
# Module-level autouse fixture: reset shared ContextVars before each test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_context_vars():  # type: ignore[return]
    """Reset the shared request-session and user-id ContextVars.

    The full test suite may leave these set (some tests use the real
    DatabaseSessionMiddleware path which sets them).  Without this reset,
    tests that assert the vars are None at entry would fail non-
    deterministically depending on test ordering.
    """
    session_token = _request_session.set(None)
    user_id_token = _current_user_id.set(None)
    schema_token = _current_tenant_schema.set(None)
    yield
    _request_session.reset(session_token)
    _current_user_id.reset(user_id_token)
    _current_tenant_schema.reset(schema_token)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_session() -> MagicMock:
    """Build a minimal Session mock whose ``new`` / ``dirty`` / ``deleted``
    attributes default to empty (no pending changes)."""
    session = MagicMock()
    session.new = []
    session.dirty = []
    session.deleted = []
    return session


@contextmanager
def _patch_standalone(mock_session: MagicMock | None = None):  # type: ignore[return]
    """Patch create_standalone_session and the RLS execute call.

    Yields the mock session so tests can introspect it.
    """
    if mock_session is None:
        mock_session = _make_mock_session()
    with patch(
        "app.db.tenant_session.create_standalone_session",
        return_value=mock_session,
    ) as mock_factory:
        yield mock_session, mock_factory


# ---------------------------------------------------------------------------
# tenant_db_session — ContextVar lifecycle
# ---------------------------------------------------------------------------


class TestTenantDbSessionContextVars:
    """Verify that ContextVars are set on entry and restored on exit."""

    def test_request_session_is_set_inside_context(self) -> None:
        with _patch_standalone() as (_mock_session, _):
            assert _request_session.get() is None
            with tenant_db_session("practice_abc", "user-1") as session:
                assert _request_session.get() is session

    def test_request_session_is_cleared_after_context(self) -> None:
        prev = _request_session.get()
        with _patch_standalone(), tenant_db_session("practice_abc", "user-1"):
            pass
        assert _request_session.get() is prev

    def test_user_id_contextvar_is_set_inside_context(self) -> None:
        with _patch_standalone():
            assert _current_user_id.get() is None
            with tenant_db_session("practice_abc", "user-42"):
                assert _current_user_id.get() == "user-42"

    def test_user_id_contextvar_is_restored_after_context(self) -> None:
        prev_token = _current_user_id.set("outer-user")
        try:
            with _patch_standalone(), tenant_db_session("practice_abc", "inner-user"):
                assert _current_user_id.get() == "inner-user"
            # After the context manager exits the token is reset, so the
            # outer value is visible again.
            assert _current_user_id.get() == "outer-user"
        finally:
            _current_user_id.reset(prev_token)

    def test_tenant_schema_contextvar_is_restored_after_context(self) -> None:
        # create_standalone_session() arms _current_tenant_schema via
        # set_tenant_schema in real code; simulate that so we can verify
        # tenant_db_session restores it on exit. A stale value would ride the
        # next pool checkout (the Engine "checkout" listener reads this var) —
        # the exact connection-leak class this primitive must not introduce.
        prev_token = _current_tenant_schema.set("practice_outer")
        try:
            mock_session = _make_mock_session()

            def _arm_schema_and_return(schema: str) -> MagicMock:
                _current_tenant_schema.set(schema)
                return mock_session

            with patch(
                "app.db.tenant_session.create_standalone_session",
                side_effect=_arm_schema_and_return,
            ):
                with tenant_db_session("practice_inner", "user-1"):
                    assert _current_tenant_schema.get() == "practice_inner"
                # Restored to the outer value, not left at practice_inner.
                assert _current_tenant_schema.get() == "practice_outer"
        finally:
            _current_tenant_schema.reset(prev_token)

    def test_context_vars_restored_on_exception(self) -> None:
        """ContextVars must be cleared even when the body raises."""
        with _patch_standalone() as (mock_session, _):
            mock_session.new = [object()]  # trigger assert_tenant_schema_set path
            raised = False
            try:
                with tenant_db_session("practice_abc", "user-1"):
                    raise RuntimeError("bang")
            except RuntimeError:
                raised = True
            assert raised  # the error must propagate, not be swallowed
        assert _request_session.get() is None
        assert _current_user_id.get() is None


# ---------------------------------------------------------------------------
# tenant_db_session — session lifecycle (commit / rollback / close)
# ---------------------------------------------------------------------------


class TestTenantDbSessionLifecycle:
    """Verify commit/rollback/close behaviour."""

    def test_commits_on_clean_exit(self) -> None:
        with _patch_standalone() as (mock_session, _):
            with tenant_db_session("practice_abc", "user-1"):
                pass
            mock_session.commit.assert_called_once()
            mock_session.rollback.assert_not_called()
            mock_session.close.assert_called_once()

    def test_rolls_back_on_exception(self) -> None:
        with _patch_standalone() as (mock_session, _):
            raised = False
            try:
                with tenant_db_session("practice_abc", "user-1"):
                    raise ValueError("oops")
            except ValueError:
                raised = True
            assert raised  # the error must propagate, not be swallowed
            mock_session.rollback.assert_called_once()
            mock_session.commit.assert_not_called()
            mock_session.close.assert_called_once()

    def test_closes_session_after_commit(self) -> None:
        with _patch_standalone() as (mock_session, _):
            with tenant_db_session("practice_abc", "user-1"):
                pass
            # close must come after commit
            call_order = mock_session.mock_calls
            commit_idx = call_order.index(call.commit())
            close_idx = call_order.index(call.close())
            assert close_idx > commit_idx

    def test_closes_session_after_rollback(self) -> None:
        with _patch_standalone() as (mock_session, _):
            try:
                with tenant_db_session("practice_abc", "user-1"):
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            call_order = mock_session.mock_calls
            rollback_idx = call_order.index(call.rollback())
            close_idx = call_order.index(call.close())
            assert close_idx > rollback_idx


# ---------------------------------------------------------------------------
# tenant_db_session — schema scoping
# ---------------------------------------------------------------------------


class TestTenantDbSessionSchema:
    """Verify that the correct schema is passed to create_standalone_session."""

    def test_passes_schema_to_standalone_session(self) -> None:
        with _patch_standalone() as (_, mock_factory):  # noqa: SIM117
            with tenant_db_session("practice_xyz789", "user-1"):
                pass
        mock_factory.assert_called_once_with("practice_xyz789")

    def test_rls_guc_is_set_for_user(self) -> None:
        """The first execute inside the context sets app.current_user_id."""
        with _patch_standalone() as (mock_session, _):  # noqa: SIM117
            with tenant_db_session("practice_abc", "user-99"):
                pass

        # find the set_config call — it's the first execute() on the session
        execute_calls = [c for c in mock_session.mock_calls if c[0] == "execute"]
        assert len(execute_calls) >= 1
        first_call_args = execute_calls[0][1]
        # The SQL text should contain set_config
        assert "set_config" in str(first_call_args[0])
        # The bind params should carry the user id
        first_call_kwargs: dict[str, Any] = execute_calls[0][2] if execute_calls[0][2] else {}
        bind_params: dict[str, Any] = (
            execute_calls[0][1][1] if len(execute_calls[0][1]) > 1 else first_call_kwargs
        )
        assert bind_params.get("uid") == "user-99"


# ---------------------------------------------------------------------------
# tenant_db_session — fail-closed guard
# ---------------------------------------------------------------------------


class TestTenantDbSessionFailClosed:
    """Verify assert_tenant_schema_set is invoked when there are pending changes."""

    def test_assert_tenant_schema_called_when_dirty(self) -> None:
        with _patch_standalone() as (mock_session, _):
            mock_session.dirty = [object()]  # simulate pending update
            with patch("app.db.tenant_session.assert_tenant_schema_set") as mock_guard:
                with tenant_db_session("practice_abc", "user-1"):
                    pass
                mock_guard.assert_called_once()

    def test_assert_tenant_schema_called_when_new(self) -> None:
        with _patch_standalone() as (mock_session, _):
            mock_session.new = [object()]  # simulate pending insert
            with patch("app.db.tenant_session.assert_tenant_schema_set") as mock_guard:
                with tenant_db_session("practice_abc", "user-1"):
                    pass
                mock_guard.assert_called_once()

    def test_assert_tenant_schema_called_when_deleted(self) -> None:
        with _patch_standalone() as (mock_session, _):
            mock_session.deleted = [object()]  # simulate pending delete
            with patch("app.db.tenant_session.assert_tenant_schema_set") as mock_guard:
                with tenant_db_session("practice_abc", "user-1"):
                    pass
                mock_guard.assert_called_once()

    def test_no_guard_call_when_no_pending_changes(self) -> None:
        with _patch_standalone() as (mock_session, _):
            # default: new/dirty/deleted are empty lists
            assert not mock_session.new
            assert not mock_session.dirty
            assert not mock_session.deleted
            with patch("app.db.tenant_session.assert_tenant_schema_set") as mock_guard:
                with tenant_db_session("practice_abc", "user-1"):
                    pass
                mock_guard.assert_not_called()

    def test_guard_raising_triggers_rollback(self) -> None:
        with _patch_standalone() as (mock_session, _):
            mock_session.new = [object()]
            with (  # noqa: SIM117
                patch(
                    "app.db.tenant_session.assert_tenant_schema_set",
                    side_effect=RuntimeError("tenant slip!"),
                ),
                pytest.raises(RuntimeError, match="tenant slip"),
            ):
                with tenant_db_session("practice_abc", "user-1"):
                    pass
            mock_session.rollback.assert_called_once()
            mock_session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# tenant_db_session — nested usage (ContextVar token restore)
# ---------------------------------------------------------------------------


class TestTenantDbSessionNested:
    """Verify that nested uses don't clobber the outer context."""

    def test_outer_session_restored_after_inner_exits(self) -> None:
        outer_session = _make_mock_session()
        inner_session = _make_mock_session()

        with patch(
            "app.db.tenant_session.create_standalone_session",
            side_effect=[outer_session, inner_session],
        ), tenant_db_session("practice_outer", "user-outer") as s_outer:
            assert _request_session.get() is s_outer
            with tenant_db_session("practice_inner", "user-inner") as s_inner:
                assert _request_session.get() is s_inner
            # inner has exited — outer should be visible again
            assert _request_session.get() is s_outer
        # both have exited — cleared
        assert _request_session.get() is None

    def test_outer_user_id_restored_after_inner_exits(self) -> None:
        outer_session = _make_mock_session()
        inner_session = _make_mock_session()

        with patch(
            "app.db.tenant_session.create_standalone_session",
            side_effect=[outer_session, inner_session],
        ), tenant_db_session("practice_outer", "user-outer"):
            assert _current_user_id.get() == "user-outer"
            with tenant_db_session("practice_inner", "user-inner"):
                assert _current_user_id.get() == "user-inner"
            assert _current_user_id.get() == "user-outer"
        assert _current_user_id.get() is None


# ---------------------------------------------------------------------------
# run_in_tenant
# ---------------------------------------------------------------------------


class TestRunInTenant:
    """Verify the async worker-thread helper."""

    def test_passes_session_as_first_arg(self) -> None:
        received: list[Any] = []

        def _fn(session: Any) -> None:
            received.append(session)

        mock_session = _make_mock_session()
        with patch(
            "app.db.tenant_session.create_standalone_session",
            return_value=mock_session,
        ):
            asyncio.run(run_in_tenant("practice_abc", "user-1", _fn))

        assert len(received) == 1
        assert received[0] is mock_session

    def test_forwards_extra_args_and_kwargs(self) -> None:
        received: list[Any] = []

        def _fn(session: Any, x: int, y: str) -> None:
            received.append((x, y))

        mock_session = _make_mock_session()
        with patch(
            "app.db.tenant_session.create_standalone_session",
            return_value=mock_session,
        ):
            asyncio.run(run_in_tenant("practice_abc", "user-1", _fn, 42, y="hello"))

        assert received == [(42, "hello")]

    def test_propagates_return_value(self) -> None:
        def _fn(session: Any) -> str:
            return "done"

        mock_session = _make_mock_session()
        with patch(
            "app.db.tenant_session.create_standalone_session",
            return_value=mock_session,
        ):
            result = asyncio.run(run_in_tenant("practice_abc", "user-1", _fn))

        assert result == "done"

    def test_propagates_exception_from_fn(self) -> None:
        def _fn(session: Any) -> None:
            raise ValueError("worker failure")

        mock_session = _make_mock_session()
        with patch(
            "app.db.tenant_session.create_standalone_session",
            return_value=mock_session,
        ), pytest.raises(ValueError, match="worker failure"):
            asyncio.run(run_in_tenant("practice_abc", "user-1", _fn))

    def test_session_is_rolled_back_when_fn_raises(self) -> None:
        def _fn(session: Any) -> None:
            raise RuntimeError("boom")

        mock_session = _make_mock_session()
        with patch(
            "app.db.tenant_session.create_standalone_session",
            return_value=mock_session,
        ), pytest.raises(RuntimeError):
            asyncio.run(run_in_tenant("practice_abc", "user-1", _fn))

        mock_session.rollback.assert_called_once()
        mock_session.commit.assert_not_called()

    def test_session_is_committed_on_clean_exit(self) -> None:
        def _fn(session: Any) -> None:
            pass

        mock_session = _make_mock_session()
        with patch(
            "app.db.tenant_session.create_standalone_session",
            return_value=mock_session,
        ):
            asyncio.run(run_in_tenant("practice_abc", "user-1", _fn))

        mock_session.commit.assert_called_once()
        mock_session.close.assert_called_once()

    def test_runs_on_worker_thread_not_event_loop(self) -> None:
        """The DB work must not block the event loop thread."""
        event_loop_thread_id = threading.current_thread().ident
        worker_thread_ids: list[int | None] = []

        def _fn(session: Any) -> None:
            worker_thread_ids.append(threading.current_thread().ident)

        mock_session = _make_mock_session()
        with patch(
            "app.db.tenant_session.create_standalone_session",
            return_value=mock_session,
        ):
            asyncio.run(run_in_tenant("practice_abc", "user-1", _fn))

        assert len(worker_thread_ids) == 1
        assert worker_thread_ids[0] != event_loop_thread_id


# ---------------------------------------------------------------------------
# Public export surface
# ---------------------------------------------------------------------------


def test_importable_from_db_package() -> None:
    """Both helpers must be importable directly from app.db."""
    from app.db import run_in_tenant as _rit  # noqa: PLC0415
    from app.db import tenant_db_session as _tds  # noqa: PLC0415

    assert callable(_tds)
    assert callable(_rit)
