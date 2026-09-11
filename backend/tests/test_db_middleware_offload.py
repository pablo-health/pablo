# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The request prelude in DatabaseSessionMiddleware must not run on the event loop.

Regression cover for PABLO-pjdb. ``dispatch`` is ``async``, so the synchronous
work it does before ``call_next`` — verifying the credential against the
identity provider, resolving the practice schema, arming ``search_path`` —
executes on the event loop unless it is explicitly offloaded. When the provider
stalled on 2026-09-10 that blocked the entire worker for ~152s: every in-flight
request froze, nothing logged for 62 of those seconds, and Postgres reaped the
pool's idle-in-transaction connections underneath requests that could not
commit.

The tests below pin the three properties that keep that from recurring: the
prelude runs on a worker thread, a slow prelude does not stop unrelated work on
the loop, and the tenant-schema ContextVar still reaches the request afterwards
despite being written inside the thread.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from app.db import _current_tenant_schema, _request_session
from app.db.middleware import DatabaseSessionMiddleware

RESOLVED_SCHEMA = "practice_abc"


def _make_mock_session() -> MagicMock:
    session = MagicMock()
    session.new = []
    session.dirty = []
    session.deleted = []
    return session


def _make_request() -> MagicMock:
    """A request whose ``state`` behaves like the real attribute bag."""
    request = MagicMock()
    request.state = type("State", (), {})()
    return request


@pytest.fixture(autouse=True)
def _reset_context_vars():  # type: ignore[return]
    """Keep ContextVar leakage between tests from making these order-dependent."""
    session_token = _request_session.set(None)
    schema_token = _current_tenant_schema.set(None)
    yield
    _request_session.reset(session_token)
    _current_tenant_schema.reset(schema_token)


async def _run_dispatch(
    prelude_side_effect: Any = None,
    schema_seen_by_route: list[str | None] | None = None,
) -> tuple[MagicMock, list[int | None]]:
    """Drive ``dispatch`` once with the prelude's internals patched out.

    Returns the session the middleware built and the thread ids the prelude
    observed itself running on. When ``schema_seen_by_route`` is given, the
    stand-in for the route records the tenant-schema ContextVar as a real
    handler would see it.
    """
    prelude_thread_ids: list[int | None] = []
    mock_session = _make_mock_session()

    def _fake_verify(request: Any) -> None:
        prelude_thread_ids.append(threading.current_thread().ident)
        if prelude_side_effect is not None:
            prelude_side_effect()

    async def _call_next(request: Any) -> MagicMock:
        if schema_seen_by_route is not None:
            schema_seen_by_route.append(_current_tenant_schema.get())
        response = MagicMock()
        response.status_code = 200
        return response

    middleware = DatabaseSessionMiddleware(app=MagicMock())
    with (
        patch("app.db.middleware.get_session_factory", return_value=lambda: mock_session),
        patch("app.db.middleware._verify_and_stash_clinician_identity", _fake_verify),
        patch(
            "app.db.middleware._resolve_schema_from_request",
            return_value=(RESOLVED_SCHEMA, "resolved"),
        ),
        patch("app.db.middleware.set_tenant_schema") as mock_set_schema,
    ):
        # Mirror the real function: it writes the ContextVar, and that write is
        # exactly what the threadpool hop discards.
        mock_set_schema.side_effect = lambda _session, schema: _current_tenant_schema.set(schema)
        await middleware.dispatch(_make_request(), _call_next)

    return mock_session, prelude_thread_ids


class TestPreludeRunsOffTheEventLoop:
    """The acute fix: blocking work belongs on a worker thread."""

    def test_prelude_runs_on_a_worker_thread(self) -> None:
        loop_thread_id = threading.current_thread().ident

        _, prelude_thread_ids = asyncio.run(_run_dispatch())

        assert len(prelude_thread_ids) == 1
        assert prelude_thread_ids[0] != loop_thread_id

    def test_slow_prelude_does_not_stall_the_loop(self) -> None:
        """A stalled provider must cost one request, not the whole worker.

        Runs the middleware against a prelude that blocks for 300ms while a
        plain coroutine ticks on the loop. Before the offload the ticker could
        not run at all until the prelude returned; now it keeps going. The
        assertion is deliberately loose (>=3 ticks of a 10ms sleep inside a
        300ms block) so it reports the difference between "loop is live" and
        "loop is frozen" rather than the machine's timing jitter.
        """
        ticks = 0

        async def _ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        async def _scenario() -> None:
            ticker_task = asyncio.create_task(_ticker())
            try:
                await _run_dispatch(prelude_side_effect=lambda: time.sleep(0.3))
            finally:
                ticker_task.cancel()

        asyncio.run(_scenario())

        assert ticks >= 3, f"event loop appears blocked during the prelude (ticks={ticks})"


class TestTenantSchemaContextVarSurvivesTheHop:
    """The subtle part: a ContextVar written in the worker dies with it."""

    def test_route_sees_the_resolved_schema(self) -> None:
        """``set_tenant_schema`` writes the ContextVar inside the worker thread.

        ``run_in_threadpool`` hands the worker a COPY of the context, so that
        write never reaches the loop on its own. The middleware has to re-apply
        it before ``call_next``, or the pool-checkout listener loses the schema
        it re-arms ``search_path`` from on any later connection grab — a
        tenant-isolation-relevant backstop going quietly missing.

        Asserted where it matters: what a route handler actually observes.
        Deleting the re-apply in ``dispatch`` turns this None.
        """
        schema_seen_by_route: list[str | None] = []

        asyncio.run(_run_dispatch(schema_seen_by_route=schema_seen_by_route))

        assert schema_seen_by_route == [RESOLVED_SCHEMA]
