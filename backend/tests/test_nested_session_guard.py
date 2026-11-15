# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Opening a session while this context holds a pooled connection is refused in tests.

Waiting for a connection while holding one from the same pool is how a burst
deadlocks the pool. ``app.db._check_nested_open`` raises on it under test
(``tests/conftest.py`` turns that on) and logs once per call site on a server.
These cases pin which combinations count: anything onto the main pool while
holding; a lookup while holding a lookup; and a lookup while holding only a
main-pool connection, which is what the lookup pool is for, does not.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import anyio.to_thread
import pytest
from app import db, main
from app.db import (
    NestedSessionError,
    allow_nested_session,
    create_lookup_session,
    create_standalone_session,
    pool_stats,
    raise_on_nested_sessions,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


def _session() -> MagicMock:
    """A session stand-in that holds a connection once it is "queried"."""
    session = MagicMock()
    session.in_transaction.return_value = False
    return session


def _query(session: MagicMock) -> MagicMock:
    session.in_transaction.return_value = True
    return session


def _assert_still_held(held: MagicMock, pool: str) -> None:
    """The guard tracks opened sessions by weakref, so a test must keep its own.

    A MagicMock and its child mocks form a cycle; left unreferenced, the
    stand-in lives only until the next cyclic GC, and a case built on it then
    fails at random or passes without the guard ever seeing a holder. Forcing
    the GC here makes that deterministic: the caller's reference is all that
    keeps *held* counted.
    """
    gc.collect()
    assert db._held_pools() == [pool]
    assert held.in_transaction()


@pytest.fixture(autouse=True)
def _factories() -> Iterator[None]:
    """Each factory call hands out a fresh stand-in; the ContextVars start empty."""
    main = MagicMock(side_effect=_session)
    lookup = MagicMock(side_effect=_session)
    opened = db._opened_sessions.set(())
    request = db._request_session.set(None)
    displaced = db._displaced_sessions.set(())
    with (
        patch.object(db, "get_session_factory", return_value=main),
        patch.object(db, "get_lookup_session_factory", return_value=lookup),
    ):
        yield
    db._displaced_sessions.reset(displaced)
    db._request_session.reset(request)
    db._opened_sessions.reset(opened)


def test_guard_is_on_for_the_unit_suite() -> None:
    assert db._raise_on_nested_session is True


def test_standalone_inside_a_holding_standalone_raises() -> None:
    held = _query(create_standalone_session())
    _assert_still_held(held, db._POOL_MAIN)
    with pytest.raises(NestedSessionError, match="main-pool"):
        create_standalone_session()


def test_standalone_while_the_request_session_holds_raises() -> None:
    db._request_session.set(_query(_session()))
    with pytest.raises(NestedSessionError, match="test_nested_session_guard"):
        create_standalone_session()


def test_standalone_while_a_displaced_session_holds_raises() -> None:
    db._displaced_sessions.set((_query(_session()),))
    with pytest.raises(NestedSessionError):
        create_standalone_session()


def test_lookup_while_the_request_session_holds_is_what_the_lookup_pool_is_for() -> None:
    db._request_session.set(_query(_session()))
    create_lookup_session()


def test_lookup_inside_a_holding_standalone_is_allowed() -> None:
    held = _query(create_standalone_session())
    _assert_still_held(held, db._POOL_MAIN)
    create_lookup_session()


def test_lookup_inside_a_holding_lookup_raises() -> None:
    held = _query(create_lookup_session())
    _assert_still_held(held, db._POOL_LOOKUP)
    with pytest.raises(NestedSessionError, match="lookup-pool session while this context holds"):
        create_lookup_session()


def test_standalone_inside_a_holding_lookup_raises() -> None:
    held = _query(create_lookup_session())
    _assert_still_held(held, db._POOL_LOOKUP)
    with pytest.raises(NestedSessionError):
        create_standalone_session()


def test_a_session_that_holds_nothing_does_not_count() -> None:
    """Opened but never queried, or committed: no connection held, nothing to wait on."""
    create_standalone_session()
    db._request_session.set(_session())
    create_standalone_session()
    create_lookup_session()
    create_lookup_session()


def test_a_released_session_stops_counting() -> None:
    first = _query(create_standalone_session())
    first.in_transaction.return_value = False  # committed / closed
    create_standalone_session()


def test_allow_nested_session_permits_a_reviewed_path_and_only_inside_it() -> None:
    held = _query(create_standalone_session())
    _assert_still_held(held, db._POOL_MAIN)
    with allow_nested_session("provisioning step"):
        create_standalone_session()
    with pytest.raises(NestedSessionError):
        create_standalone_session()


def test_a_mock_bound_session_is_not_mistaken_for_a_holder() -> None:
    """Unit tests bind bare MagicMocks, whose in_transaction() is a truthy mock."""
    db._request_session.set(MagicMock())
    create_standalone_session()


def test_server_mode_logs_once_per_call_site_and_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    db._request_session.set(_query(_session()))
    raise_on_nested_sessions(enabled=False)
    try:
        db._nested_sessions_logged.clear()
        with caplog.at_level(logging.WARNING, logger="app.db"):
            for _ in range(3):
                create_standalone_session()
    finally:
        raise_on_nested_sessions()
        db._nested_sessions_logged.clear()
    lines = [r for r in caplog.records if getattr(r, "event", None) == "nested_db_session"]
    assert len(lines) == 1
    assert lines[0].opening == "main"  # type: ignore[attr-defined]
    assert lines[0].held == ["main"]  # type: ignore[attr-defined]
    assert "test_nested_session_guard" in lines[0].at  # type: ignore[attr-defined]


def test_server_thread_limit_is_applied_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: the lifespan sets AnyIO's default limiter to ``server_thread_limit``."""
    monkeypatch.setattr(main.settings, "server_thread_limit", 77)
    monkeypatch.setattr(main.settings, "database_pool_stats_interval_seconds", 0)
    seen: list[float] = []

    async def run() -> None:
        before = anyio.to_thread.current_default_thread_limiter().total_tokens
        assert before != 77
        async with main.lifespan(main.app):
            seen.append(anyio.to_thread.current_default_thread_limiter().total_tokens)

    asyncio.run(run())
    assert seen == [77]


def test_pool_stats_loop_starts_with_the_app_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[float] = []

    async def fake_loop(interval_s: float) -> None:
        started.append(interval_s)

    monkeypatch.setattr(pool_stats, "pool_stats_loop", fake_loop)
    monkeypatch.setattr(main.settings, "database_pool_stats_interval_seconds", 15)

    async def run() -> None:
        async with main.lifespan(main.app):
            await asyncio.sleep(0)

    asyncio.run(run())
    assert started == [15]
