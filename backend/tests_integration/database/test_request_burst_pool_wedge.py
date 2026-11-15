# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A burst of requests must not wedge the process's own connection pool.

Each request takes a pooled connection at its first query and keeps it until
the middleware commits. Two things let a burst stop a process cold while the
database sat mostly idle (load run, a few dozen active users, pool 15 + 5,
anyio's 40 worker threads):

1. **A second connection while holding the first.** Request paths open a
   standalone session beside their own (the practice lookup, presence,
   sign-in bookkeeping). On one shared pool, once every connection belonged to
   a request waiting for another, nothing moved until the pool timeout, and
   the wait re-formed at once under continued load.
2. **Fewer worker threads than requests.** A request keeps its connection
   across its sync hops (dependency, dependency, endpoint). Newly arrived
   requests took every thread while they waited for a connection, and the
   requests holding the connections never got a thread back to finish on.

These tests drive the real ``DatabaseSessionMiddleware``, the real engines and
``create_standalone_session`` against Postgres, scaled down: a request pool of
two, four threads before the fix, sixteen concurrent requests. The route has
the shape of the real ones: hold the request connection, open a standalone
session in a later hop, query again in the endpoint. The two controls show
the harness reproduces each wedge with the old configuration; the third test
is the fix. The last two pin which pool a standalone session gets: the nested
one only while a session bound to the context holds a connection.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import anyio.to_thread
import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SCHEMA = "practice_burst_wedge_probe"
#: Requests fired at once. More than the pool and more than the old threads.
BURST = 16
#: The request pool (size, no overflow).
REQUEST_POOL = 2
#: anyio's default is 40 against a 20-connection pool in production; this keeps
#: the same ratio at the test's scale.
OLD_THREAD_LIMIT = 2 * REQUEST_POOL
#: Seconds a checkout waits for a connection. A wedge shows as pool timeouts.
POOL_TIMEOUT_S = 3
#: The whole burst's budget. A healthy burst finishes in well under a second.
BURST_BUDGET_S = 30.0


@pytest.fixture
def probe_schema() -> Iterator[None]:
    engine = create_engine(_db_url)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}"))
    try:
        yield
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        engine.dispose()


def _clear_engine_caches() -> None:
    from app import db  # noqa: PLC0415
    from app.settings import get_settings  # noqa: PLC0415

    for cached in (db.get_nested_session_factory, db.get_session_factory):
        cached.cache_clear()
    for engine_fn in (db.get_nested_engine, db.get_engine):
        if engine_fn.cache_info().currsize:
            engine_fn().dispose()
        engine_fn.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def small_pools(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Engines built by the real code from settings, with a tiny request pool.

    The test sets ``DATABASE_NESTED_POOL_SIZE`` itself before the first
    request; the engines are built lazily on first use.
    """
    monkeypatch.setenv("DATABASE_POOL_SIZE", str(REQUEST_POOL))
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", str(POOL_TIMEOUT_S))
    _clear_engine_caches()
    yield monkeypatch
    _clear_engine_caches()
    monkeypatch.undo()
    _clear_engine_caches()


def _arm(_request: object, session: Session) -> tuple[str, None]:
    """The prelude without the identity provider: a standalone practice lookup, then arm."""
    from app.db import create_standalone_session, set_tenant_schema  # noqa: PLC0415

    with create_standalone_session() as lookup:
        lookup.execute(text("SELECT 1"))
    set_tenant_schema(session, _SCHEMA)
    return _SCHEMA, None


def _app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from app.db import create_standalone_session, get_db_session  # noqa: PLC0415
    from app.db.middleware import DatabaseSessionMiddleware  # noqa: PLC0415

    monkeypatch.setattr(DatabaseSessionMiddleware, "_prepare", staticmethod(_arm))

    def holds_request_connection() -> None:
        # First query: the request session now holds a pooled connection
        # inside a transaction until the middleware commits.
        get_db_session().execute(text("SELECT 1"))

    def opens_standalone_session(_: None = Depends(holds_request_connection)) -> None:
        # A later sync hop, as get_tenant_context's practice lookup is.
        with create_standalone_session() as lookup:
            lookup.execute(text("SELECT 1"))

    application = FastAPI()
    application.add_middleware(DatabaseSessionMiddleware)

    @application.get("/burst")
    def burst(_: None = Depends(opens_standalone_session)) -> dict[str, bool]:
        get_db_session().execute(text("SELECT 1"))
        return {"ok": True}

    return application


def _fire_burst(application: FastAPI, *, raise_limit: bool) -> list[int]:
    """Fire ``BURST`` concurrent requests; the status of each."""

    async def run() -> list[int]:
        anyio.to_thread.current_default_thread_limiter().total_tokens = OLD_THREAD_LIMIT
        if raise_limit:
            from app.main import raise_worker_thread_limit  # noqa: PLC0415
            from app.settings import get_settings  # noqa: PLC0415

            raise_worker_thread_limit(get_settings().worker_thread_limit)
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.wait_for(
                asyncio.gather(*(client.get("/burst") for _ in range(BURST))),
                timeout=BURST_BUDGET_S,
            )
        return [r.status_code for r in responses]

    return asyncio.run(run())


@pytest.mark.timeout(120)
def test_control_shared_pool_wedges_on_nested_standalone_session(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """Control: nested standalone sessions on the main pool reproduce wedge 1."""
    small_pools.setenv("DATABASE_NESTED_POOL_SIZE", "0")
    statuses = _fire_burst(_app(small_pools), raise_limit=True)
    assert statuses.count(200) < BURST, statuses


@pytest.mark.timeout(120)
def test_control_too_few_threads_wedges_the_request_pool(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """Control: the nested pool alone, with the old thread limit, still wedges (2)."""
    small_pools.setenv("DATABASE_NESTED_POOL_SIZE", "1")
    statuses = _fire_burst(_app(small_pools), raise_limit=False)
    assert statuses.count(200) < BURST, statuses


@pytest.mark.timeout(120)
def test_burst_completes_with_nested_pool_and_raised_thread_limit(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """The fix: default nested pool and worker-thread limit; every request succeeds."""
    small_pools.delenv("DATABASE_NESTED_POOL_SIZE", raising=False)
    statuses = _fire_burst(_app(small_pools), raise_limit=True)
    assert statuses == [200] * BURST


def test_standalone_session_pool_follows_what_the_caller_holds(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """Nested pool only while a bound session holds a connection; main pool otherwise.

    Jobs, scripts and worker threads open standalone sessions holding nothing,
    and keep the main pool's concurrency.
    """
    from app.db import (  # noqa: PLC0415
        _request_session,
        create_standalone_session,
        get_engine,
        get_nested_engine,
        get_session_factory,
    )

    small_pools.setenv("DATABASE_NESTED_POOL_SIZE", "3")
    nested = get_nested_engine()
    assert nested is not get_engine()
    assert nested.pool.size() == 3  # type: ignore[attr-defined]

    with create_standalone_session() as job_session:
        assert job_session.get_bind() is get_engine()

    request_session = get_session_factory()()
    token = _request_session.set(request_session)
    try:
        # A request before its first query holds nothing: main pool.
        with create_standalone_session() as before_first_query:
            assert before_first_query.get_bind() is get_engine()
        request_session.execute(text("SELECT 1"))
        with create_standalone_session() as while_holding:
            assert while_holding.get_bind() is nested
    finally:
        _request_session.reset(token)
        request_session.close()


def test_tenant_unit_of_work_releases_the_request_connection_and_keeps_the_main_pool(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """``tenant_db_session`` displaces the request session; it must not occupy the nested pool."""
    from app.db import _request_session, get_engine, get_session_factory  # noqa: PLC0415
    from app.db.tenant_session import tenant_db_session  # noqa: PLC0415

    small_pools.setenv("DATABASE_NESTED_POOL_SIZE", "1")
    request_session = get_session_factory()()
    token = _request_session.set(request_session)
    try:
        request_session.execute(text("SELECT 1"))
        assert request_session.in_transaction()
        with tenant_db_session(_SCHEMA, "user-burst-probe") as unit:
            assert not request_session.in_transaction()
            assert unit.get_bind() is get_engine()
    finally:
        _request_session.reset(token)
        request_session.close()
