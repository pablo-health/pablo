# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The request session must not hold a transaction open while the route runs.

``DatabaseSessionMiddleware`` arms the request session before any route or
dependency runs: it resolves the practice schema and issues
``SET search_path``. That ``SET`` begins a transaction, and before this fix
nothing ended it until the route returned. A route that waits on something
other than the database before its first query (a model call, a request to
another service) therefore held its connection ``idle in transaction`` for as
long as it waited. Past ``idle_in_transaction_session_timeout`` the server
terminated the connection, and the commit at the end of the request failed
with ``SSL connection has been closed unexpectedly``.

These tests drive the real middleware against a real Postgres. Each case
checks two things: the route starts with no open transaction, and its first
query still runs on the schema the prelude chose.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SCHEMA = "practice_prelude_txn_probe"
#: The server terminates a connection idle inside a transaction this long.
IDLE_TIMEOUT_MS = 2000
#: How long the slow route waits before its first query: past the timeout.
SLOW_ROUTE_SECONDS = 3.0


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


@pytest.fixture
def short_timeout_sessions(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Request sessions whose connections carry a short idle-in-transaction timeout."""
    from app.db import middleware  # noqa: PLC0415

    engine = create_engine(
        _db_url,
        pool_pre_ping=True,
        connect_args={"options": f"-c idle_in_transaction_session_timeout={IDLE_TIMEOUT_MS}"},
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(middleware, "get_session_factory", lambda: factory)
    yield
    engine.dispose()


def _app(
    monkeypatch: pytest.MonkeyPatch,
    arm: Callable[[Any, Session], tuple[str | None, str | None]] | None,
    *,
    wait_seconds: float = 0.0,
) -> tuple[FastAPI, dict[str, Any]]:
    """The real middleware; ``arm`` replaces ``_prepare`` unless it is ``None``.

    The route records whether its session was inside a transaction on entry,
    then (after ``wait_seconds``) the schema its first query resolved to.
    """
    from app.db import get_db_session  # noqa: PLC0415
    from app.db.middleware import DatabaseSessionMiddleware  # noqa: PLC0415

    if arm is not None:
        monkeypatch.setattr(DatabaseSessionMiddleware, "_prepare", staticmethod(arm))

    seen: dict[str, Any] = {}
    application = FastAPI()
    application.add_middleware(DatabaseSessionMiddleware)

    @application.get("/probe")
    def probe() -> dict[str, bool]:
        session = get_db_session()
        seen["in_transaction_on_entry"] = session.in_transaction()
        if wait_seconds:
            time.sleep(wait_seconds)
        seen["search_path"] = session.execute(text("SHOW search_path")).scalar_one()
        return {"ok": True}

    return application, seen


def _arm_practice(_request: object, session: Session) -> tuple[str, None]:
    from app.db import set_tenant_schema  # noqa: PLC0415

    set_tenant_schema(session, _SCHEMA)
    return _SCHEMA, None


def _arm_no_practice(_request: object, session: Session) -> tuple[None, str]:
    from app.db import clear_tenant_schema  # noqa: PLC0415

    clear_tenant_schema(session)
    return None, "no-practice-mapping-for-identity"


@pytest.mark.usefixtures("probe_schema")
def test_resolved_practice_route_starts_outside_a_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, seen = _app(monkeypatch, _arm_practice)
    with TestClient(application) as client:
        response = client.get("/probe")
    assert response.status_code == 200
    assert seen["in_transaction_on_entry"] is False
    assert seen["search_path"].split(",")[0].strip() == _SCHEMA


def test_unauthenticated_route_starts_outside_a_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real prelude, no credential: the default schema, as an internal route gets."""
    from app.db import DEFAULT_PRACTICE_SCHEMA  # noqa: PLC0415

    application, seen = _app(monkeypatch, None)
    with TestClient(application) as client:
        response = client.get("/probe")
    assert response.status_code == 200
    assert seen["in_transaction_on_entry"] is False
    assert seen["search_path"].split(",")[0].strip() == DEFAULT_PRACTICE_SCHEMA


def test_unresolved_route_starts_outside_a_transaction_on_no_practice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, seen = _app(monkeypatch, _arm_no_practice)
    with TestClient(application) as client:
        response = client.get("/probe")
    assert response.status_code == 200
    assert seen["in_transaction_on_entry"] is False
    assert "practice" not in seen["search_path"]


@pytest.mark.usefixtures("probe_schema", "short_timeout_sessions")
def test_route_that_waits_before_its_first_query_survives_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failure as it happened: a slow route, then a commit on a terminated connection."""
    application, seen = _app(monkeypatch, _arm_practice, wait_seconds=SLOW_ROUTE_SECONDS)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/probe")
    assert response.status_code == 200
    assert seen["search_path"].split(",")[0].strip() == _SCHEMA
