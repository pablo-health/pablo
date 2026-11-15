# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The nested pool's leaf-only contract, on the real engines against Postgres.

A standalone session opened while the request holds a connection comes from a
small nested pool (``app.db.get_nested_engine``). That is only safe for a leaf:
a few reads, then close. Three things hold it to that:

- the guard in ``create_standalone_session`` (raised on for this suite by the
  ``tests_integration`` conftest), which the real practice-facts and sender
  lookups pass, and which a nested open inside them trips;
- a short checkout timeout on the nested pool, so an exhausted one fails in
  about ``database_nested_pool_timeout_seconds``, not the main pool's 30 s;
- a ``db_pool_stats`` line per pool, which counts that timeout.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, exc, text

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

#: The main pool's checkout timeout in these tests: what a nested checkout must beat.
MAIN_POOL_TIMEOUT_S = 30
#: ``database_nested_pool_timeout_seconds``'s default.
NESTED_TIMEOUT_S = 5.0


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
def default_engines(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The app's real engines from settings: nested pool of 2, its default timeout."""
    monkeypatch.delenv("DATABASE_NESTED_POOL_SIZE", raising=False)
    monkeypatch.delenv("DATABASE_NESTED_POOL_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", str(MAIN_POOL_TIMEOUT_S))
    _clear_engine_caches()
    yield
    monkeypatch.undo()
    _clear_engine_caches()


@pytest.fixture
def practice_id() -> Iterator[str]:
    """A practice row that sends its clients the video link. Platform rows only."""
    stamp = uuid.uuid4().hex[:8]
    made = f"nested-contract-{stamp}"
    engine = create_engine(_db_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices (id, name, schema_name, owner_email,"
                " owner_user_id, product, status, is_active, created_at,"
                " send_clients_video_link)"
                " VALUES (:i, 'Nested Contract Practice', :s, 'owner@example.com', :o,"
                " 'pablo', 'active', true, now(), true)"
            ),
            {"i": made, "s": f"practice_nested_contract_{stamp}", "o": str(uuid.uuid4())},
        )
    try:
        yield made
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": made})
        engine.dispose()


@contextmanager
def _request_holding_a_connection() -> Iterator[Session]:
    """Bind a main-pool session that holds a connection, as a request past its first query."""
    from app.db import (  # noqa: PLC0415
        get_session_factory,
        publish_request_session,
        restore_request_session,
    )

    session = get_session_factory()()
    session.execute(text("SELECT 1"))
    binding = publish_request_session(session)
    try:
        yield session
    finally:
        restore_request_session(binding)
        session.rollback()
        session.close()


def _nested_checked_out() -> int:
    from app.db import get_nested_engine  # noqa: PLC0415

    return get_nested_engine().pool.checkedout()  # type: ignore[attr-defined]


def test_the_real_practice_facts_and_sender_lookups_pass_the_guard(
    default_engines: None, practice_id: str
) -> None:
    """``read_practice_facts`` and ``resolve_client_sender``, unstubbed, inside a request.

    Route tests stub both (a ``practice_facts`` lambda, an in-memory sender).
    Here they run for real while a request holds its connection, so their
    sessions come from the nested pool, with the guard raising. Each is a leaf,
    opened and closed in turn, so the guard stays quiet. The practice sends the
    link, so the facts lookup reaches the sender lookup too.
    """
    from app.db import create_standalone_session  # noqa: PLC0415
    from app.portal.client_sender import resolve_client_sender  # noqa: PLC0415
    from app.services.client_video_link_notice import read_practice_facts  # noqa: PLC0415

    with _request_holding_a_connection():
        facts = read_practice_facts(practice_id)
        sender = resolve_client_sender(practice_id)
        # Their sessions were nested-pool ones, and they gave them back.
        assert _nested_checked_out() == 0
        with create_standalone_session() as probe:
            probe.execute(text("SELECT 1"))
            assert _nested_checked_out() == 1, "a lookup here is not on the nested pool"

    assert facts is not None
    assert facts.sends_link is True
    assert facts.from_address == sender.from_address


def test_a_standalone_open_beside_a_held_nested_session_raises(default_engines: None) -> None:
    """The guard on real sessions: hold a nested one, open another, refused before checkout."""
    from app.db import NestedPoolContractError, create_standalone_session  # noqa: PLC0415

    with _request_holding_a_connection(), create_standalone_session() as lookup:
        lookup.execute(text("SELECT 1"))
        with pytest.raises(NestedPoolContractError):
            create_standalone_session()
        assert _nested_checked_out() == 1, "the refused open took a connection"


def test_a_closed_nested_session_frees_the_next_open(default_engines: None) -> None:
    """Close the leaf and the next lookup is fine: the guard tracks holding, not history."""
    from app.db import create_standalone_session  # noqa: PLC0415

    with _request_holding_a_connection():
        with create_standalone_session() as first:
            first.execute(text("SELECT 1"))
        with create_standalone_session() as second:
            second.execute(text("SELECT 1"))


@pytest.mark.timeout(60)
def test_an_exhausted_nested_pool_fails_in_its_own_timeout(
    default_engines: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A burst that finds both nested connections held gives up in about 5 s, not 30.

    Two requests each hold a lookup open (the contract broken by staying open,
    which no guard can see). Two more arrive and ask for a lookup. Both fail
    with a pool timeout inside the nested pool's own timeout, and the stats
    line counts the timeouts on the ``nested`` pool and names the ``main`` one.
    """
    from app.db import MAIN_POOL, NESTED_POOL, create_standalone_session  # noqa: PLC0415
    from app.db.pool_stats import POOL_STATS_EVENT, log_pool_stats  # noqa: PLC0415

    log_pool_stats()  # start the counters from zero
    holders_in = threading.Barrier(3, timeout=20)
    let_go = threading.Event()
    waits: list[tuple[str, float]] = []
    lock = threading.Lock()

    def holder() -> None:
        with _request_holding_a_connection(), create_standalone_session() as lookup:
            lookup.execute(text("SELECT 1"))
            holders_in.wait()
            let_go.wait(timeout=30)

    def latecomer() -> None:
        with _request_holding_a_connection():
            started = time.monotonic()
            outcome = "ok"
            try:
                with create_standalone_session() as lookup:
                    lookup.execute(text("SELECT 1"))
            except exc.TimeoutError:
                outcome = "timeout"
            with lock:
                waits.append((outcome, time.monotonic() - started))

    holders = [threading.Thread(target=holder) for _ in range(2)]
    for thread in holders:
        thread.start()
    holders_in.wait()
    try:
        latecomers = [threading.Thread(target=latecomer) for _ in range(2)]
        for thread in latecomers:
            thread.start()
        for thread in latecomers:
            thread.join(timeout=40)
    finally:
        let_go.set()
        for thread in holders:
            thread.join(timeout=10)

    assert [outcome for outcome, _ in waits] == ["timeout", "timeout"]
    for _, waited in waits:
        assert NESTED_TIMEOUT_S - 0.5 <= waited < NESTED_TIMEOUT_S + 5, waited

    with caplog.at_level(logging.INFO, logger="app.db.pool_stats"):
        log_pool_stats()
    lines = {
        r.pool: r  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "event", None) == POOL_STATS_EVENT
    }
    assert set(lines) == {MAIN_POOL, NESTED_POOL}
    assert lines[NESTED_POOL].timeouts == 2  # type: ignore[attr-defined]
    assert lines[NESTED_POOL].checkouts == 2  # type: ignore[attr-defined]
    assert lines[MAIN_POOL].timeouts == 0  # type: ignore[attr-defined]
    assert lines[MAIN_POOL].checkouts >= 4  # type: ignore[attr-defined]
