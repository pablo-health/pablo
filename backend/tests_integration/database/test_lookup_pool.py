# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Request-path lookups run on their own pool, so a burst cannot wedge the request pool.

Each request takes a pooled connection at its first query and keeps it until
the middleware commits. Resolving the caller's practice and account opens a
second session while the request holds that connection. When both came from
one pool, a burst of requests could each hold one connection and wait for a
second until every connection was taken: nothing moved until the pool
timeout. A separate thing stalled the same way: fewer worker threads than
requests in flight, with a request keeping its connection across thread hops.

The burst tests drive the real ``DatabaseSessionMiddleware``, the real engine
builder and ``create_lookup_session`` against Postgres, scaled down: a request
pool of 2 + 2, eight (or sixteen) concurrent requests, each holding its
request connection, opening a lookup in a later hop and querying again in the
endpoint, as ``get_tenant_context`` does. Each fix has a control showing the
harness reproduces the wedge without it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, exc, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SCHEMA = "practice_lookup_pool_probe"
#: The request pool, as AC1 states it: 2 + 2.
REQUEST_POOL_SIZE = 2
REQUEST_POOL_OVERFLOW = 2
#: Seconds a request-pool checkout waits. Production keeps SQLAlchemy's 30;
#: the tests shorten it so a control's wedge shows as timeouts in seconds.
REQUEST_POOL_TIMEOUT_S = 3.0
#: Requests fired at once: more than the request pool holds.
BURST = 8
#: A healthy burst finishes in well under a second.
BURST_BUDGET_S = 60.0


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

    for factory in (db.get_lookup_session_factory, db.get_session_factory):
        factory.cache_clear()
    for engine_fn in (db.get_lookup_engine, db.get_engine):
        if engine_fn.cache_info().currsize:
            engine_fn().dispose()
        engine_fn.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def small_pools(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """The real engines, built from settings, with a 2 + 2 request pool.

    The request engine goes through the same builder as production with only
    its pool timeout shortened (see ``REQUEST_POOL_TIMEOUT_S``). The lookup
    engine is ``get_lookup_engine`` unchanged unless a test sets its env.
    Engines are built lazily, so a test may set env before its first request.
    """
    from app import db  # noqa: PLC0415

    monkeypatch.setenv("DATABASE_POOL_SIZE", str(REQUEST_POOL_SIZE))
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", str(REQUEST_POOL_OVERFLOW))
    _clear_engine_caches()
    built: list[Engine] = []

    def request_engine() -> Engine:
        if not built:
            settings = db.get_settings()
            built.append(
                db._build_engine(
                    settings,
                    pool_name="main",
                    pool_size=settings.database_pool_size,
                    max_overflow=settings.database_max_overflow,
                    pool_timeout=REQUEST_POOL_TIMEOUT_S,
                )
            )
        return built[0]

    monkeypatch.setattr(db, "get_engine", request_engine)
    yield monkeypatch
    for engine in built:
        engine.dispose()
    monkeypatch.undo()
    _clear_engine_caches()


def _arm(_request: object, session: Session) -> tuple[str, None]:
    """The prelude without the identity provider: a practice lookup, then arm."""
    from app.db import create_lookup_session, set_tenant_schema  # noqa: PLC0415

    with create_lookup_session() as lookup:
        lookup.execute(text("SELECT 1"))
    set_tenant_schema(session, _SCHEMA)
    return _SCHEMA, None


def _app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    from app.db import create_lookup_session, get_db_session  # noqa: PLC0415
    from app.db.middleware import DatabaseSessionMiddleware  # noqa: PLC0415

    monkeypatch.setattr(DatabaseSessionMiddleware, "_prepare", staticmethod(_arm))

    def holds_request_connection() -> None:
        # First query: the request session now holds a pooled connection
        # inside a transaction until the middleware commits.
        get_db_session().execute(text("SELECT 1"))

    def looks_up_practice(_: None = Depends(holds_request_connection)) -> None:
        # A later sync hop, as get_tenant_context's practice lookup is.
        with create_lookup_session() as lookup:
            lookup.execute(text("SELECT 1"))

    application = FastAPI()
    application.add_middleware(DatabaseSessionMiddleware)

    @application.get("/burst")
    def burst(_: None = Depends(looks_up_practice)) -> dict[str, bool]:
        get_db_session().execute(text("SELECT 1"))
        return {"ok": True}

    return application


def _fire_burst(
    application: FastAPI, *, count: int = BURST, thread_limit: int | None = None
) -> tuple[list[int], float]:
    """Fire *count* concurrent requests: the status of each, and the wall time."""

    async def run() -> tuple[list[int], float]:
        if thread_limit is not None:
            from app.main import apply_server_thread_limit  # noqa: PLC0415

            apply_server_thread_limit(thread_limit)
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        started = time.monotonic()
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.wait_for(
                asyncio.gather(*(client.get("/burst") for _ in range(count))),
                timeout=BURST_BUDGET_S,
            )
        return [r.status_code for r in responses], time.monotonic() - started

    return asyncio.run(run())


@pytest.mark.timeout(120)
def test_burst_completes_with_lookups_on_their_own_pool(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """AC1: 8 requests on a 2 + 2 request pool, each looking up while holding: all succeed fast."""
    statuses, elapsed = _fire_burst(_app(small_pools))
    assert statuses == [200] * BURST
    assert elapsed < REQUEST_POOL_TIMEOUT_S, elapsed


@pytest.mark.timeout(120)
def test_control_lookups_on_the_request_pool_wedge(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """AC1 control, the old wiring: the lookup factory bound to the request engine.

    Every request-pool connection ends up held by a request waiting for a
    second one; the waits run out at the pool timeout and those requests fail.
    """
    from app import db  # noqa: PLC0415

    small_pools.setattr(db, "get_lookup_session_factory", db.get_session_factory)
    statuses, elapsed = _fire_burst(_app(small_pools))
    assert statuses.count(200) < BURST, statuses
    assert elapsed >= REQUEST_POOL_TIMEOUT_S, elapsed


#: The thread wedge needs more requests than threads; 16 against 4 threads is
#: the production ratio (160 concurrent requests, 40 threads) scaled down.
THREAD_BURST = 16
OLD_THREAD_LIMIT = 4


@pytest.mark.timeout(120)
def test_control_too_few_threads_wedges_even_with_the_lookup_pool(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """Fewer threads than requests: arrivals take every thread waiting for a connection."""
    statuses, _ = _fire_burst(_app(small_pools), count=THREAD_BURST, thread_limit=OLD_THREAD_LIMIT)
    assert statuses.count(200) < THREAD_BURST, statuses


@pytest.mark.timeout(120)
def test_burst_completes_with_server_thread_limit_above_concurrency(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """The same burst with ``server_thread_limit`` at or above the request concurrency."""
    statuses, _ = _fire_burst(_app(small_pools), count=THREAD_BURST, thread_limit=THREAD_BURST)
    assert statuses == [200] * THREAD_BURST


@pytest.mark.timeout(60)
def test_pool_stats_line_reports_both_pools_under_load(
    probe_schema: None, small_pools: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC7: after a burst, one ``db_pool_stats`` line per pool, with its checkouts."""
    from app.db.pool_stats import POOL_STATS_EVENT, log_pool_stats  # noqa: PLC0415

    log_pool_stats()  # drop counts other tests left behind
    statuses, _ = _fire_burst(_app(small_pools))
    assert statuses == [200] * BURST
    with caplog.at_level(logging.INFO, logger="app.db.pool_stats"):
        log_pool_stats()
    lines = {
        r.pool: r  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "event", None) == POOL_STATS_EVENT
    }
    assert set(lines) >= {"main", "lookup"}
    # Each request: one main-pool connection, two lookups (prelude + dependency).
    assert lines["main"].checkouts >= BURST  # type: ignore[attr-defined]
    assert lines["lookup"].checkouts >= 2 * BURST  # type: ignore[attr-defined]
    assert lines["lookup"].timeouts == 0  # type: ignore[attr-defined]
    assert lines["main"].pool_size == REQUEST_POOL_SIZE  # type: ignore[attr-defined]


@pytest.mark.timeout(60)
def test_lookup_pool_timeout_fails_fast(small_pools: pytest.MonkeyPatch) -> None:
    """AC6: with the lookup pool taken, the next lookup raises within ~5 s, not 30.

    The default ``database_lookup_pool_timeout_seconds`` is under test; only
    the pool is shrunk to one connection so one holder exhausts it.
    """
    from app.db import create_lookup_session  # noqa: PLC0415

    small_pools.setenv("DATABASE_LOOKUP_POOL_SIZE", "1")
    small_pools.setenv("DATABASE_LOOKUP_MAX_OVERFLOW", "0")
    holding = threading.Event()
    release = threading.Event()

    def hold_a_lookup() -> None:
        # Another request's lookup, in its own thread (its own context).
        with create_lookup_session() as held:
            held.execute(text("SELECT 1"))
            holding.set()
            release.wait(30)

    holder = threading.Thread(target=hold_a_lookup)
    holder.start()
    try:
        assert holding.wait(10)
        started = time.monotonic()
        with pytest.raises(exc.TimeoutError), create_lookup_session() as lookup:
            lookup.execute(text("SELECT 1"))
        waited = time.monotonic() - started
    finally:
        release.set()
        holder.join(10)
    assert 4.0 <= waited < 10.0, waited


def _connection_state(session: Session) -> dict[str, str | None]:
    row = session.execute(
        text(
            "SELECT current_setting('search_path'), current_setting('lock_timeout'), "
            "current_setting('idle_in_transaction_session_timeout'), "
            "current_setting('statement_timeout'), "
            "current_setting('app.current_user_id', true)"
        )
    ).one()
    return dict(
        zip(
            ("search_path", "lock_timeout", "idle_tx_timeout", "statement_timeout", "user"),
            row,
            strict=True,
        )
    )


def test_lookup_connection_carries_the_request_engine_state(
    probe_schema: None, small_pools: pytest.MonkeyPatch
) -> None:
    """AC2: a lookup connection is set up and reset exactly as a request connection.

    Same ``search_path`` for the schema asked for, same per-connection timeouts
    from the connect options, same RLS principal GUC from the ``after_begin``
    listener; and after checkin, the same neutral state on the next checkout.
    """
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        create_lookup_session,
        get_lookup_engine,
        get_session_factory,
        set_current_user_id,
        set_tenant_schema,
    )

    # One connection per pool, so "the next checkout" is the same connection.
    small_pools.setenv("DATABASE_POOL_SIZE", "1")
    small_pools.setenv("DATABASE_MAX_OVERFLOW", "0")
    small_pools.setenv("DATABASE_LOOKUP_POOL_SIZE", "1")
    small_pools.setenv("DATABASE_LOOKUP_MAX_OVERFLOW", "0")
    schema_token = _current_tenant_schema.set(None)
    user_token = _current_user_id.set(None)
    try:
        set_current_user_id("user-lookup-pool-probe")

        request_session = get_session_factory()()
        set_tenant_schema(request_session, _SCHEMA)
        armed_request = _connection_state(request_session)
        request_session.close()

        with create_lookup_session(practice_schema=_SCHEMA) as lookup:
            armed_lookup = _connection_state(lookup)

        assert armed_lookup == armed_request
        assert _SCHEMA in (armed_lookup["search_path"] or "")
        assert armed_lookup["user"] == "user-lookup-pool-probe"
        assert armed_lookup["lock_timeout"] not in (None, "0")

        # Checkin resets both the same way: read the raw connection with no
        # tenant or principal armed, so no listener re-applies anything.
        _current_tenant_schema.set(None)
        _current_user_id.set(None)
        from app.db import get_engine  # noqa: PLC0415

        def neutral(engine: Engine) -> tuple[str, str | None]:
            with engine.connect() as conn:
                path, user = conn.execute(
                    text(
                        "SELECT current_setting('search_path'), "
                        "current_setting('app.current_user_id', true)"
                    )
                ).one()
                return path, user

        assert neutral(get_lookup_engine()) == neutral(get_engine())
        assert _SCHEMA not in neutral(get_lookup_engine())[0]
    finally:
        _current_user_id.reset(user_token)
        _current_tenant_schema.reset(schema_token)
