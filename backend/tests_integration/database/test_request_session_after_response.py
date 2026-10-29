# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The request's database session must end, even when it is used after the response starts.

``DatabaseSessionMiddleware`` is a ``BaseHTTPMiddleware``, so ``call_next``
returns as soon as the route's response HEADERS are ready. The middleware then
commits and closes the request session. Everything that runs after that point
still runs in the route's context and still holds the same ``Session``:

* the body of a ``StreamingResponse`` that reads as it streams (the billing
  period export iterates its rows while the body is written), and
* a ``BackgroundTask`` attached to the response (the export routes write their
  disclosure audit row from one, after the body is fully sent).

A closed SQLAlchemy session is reusable: the first statement after ``close()``
silently begins a new transaction on a freshly checked-out connection. Nothing
ever ended that transaction. The connection sat ``idle in transaction`` until
the garbage collector happened to free the session, or until Postgres's
``idle_in_transaction_session_timeout`` killed the backend — and the next
request that picked the dead connection out of the pool failed with
``SSL connection has been closed unexpectedly``. A write made in that window
(the audit row) was never committed at all.

These tests drive the real middleware against a real Postgres and ask the
database, not the ORM, whether the work was finished.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from starlette.background import BackgroundTask

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

_SCHEMA = "practice_after_response_probe"


@pytest.fixture
def observer() -> Iterator[Engine]:
    """A connection outside the app, for asking Postgres what the app left behind."""
    engine = create_engine(_db_url)
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}"))
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS {_SCHEMA}.probe_rows (marker text)"))
    try:
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        engine.dispose()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> tuple[FastAPI, dict[str, Any]]:
    """The real session middleware, with only the credential lookup stood in for.

    ``seen`` collects what the routes observed: the session they were handed
    and the backend pid their post-response statement ran on.
    """
    from app.db import get_db_session, set_tenant_schema  # noqa: PLC0415
    from app.db.middleware import DatabaseSessionMiddleware  # noqa: PLC0415

    def _prepare(_request: object, session: Session) -> tuple[str, str | None]:
        set_tenant_schema(session, _SCHEMA)
        return _SCHEMA, None

    monkeypatch.setattr(DatabaseSessionMiddleware, "_prepare", staticmethod(_prepare))

    seen: dict[str, Any] = {}
    application = FastAPI()
    application.add_middleware(DatabaseSessionMiddleware)

    @application.get("/stream")
    def stream() -> StreamingResponse:
        session = get_db_session()
        seen["session"] = session

        def body() -> Iterator[bytes]:
            # Read while streaming, as an export that iterates its rows does.
            seen["pid"] = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            yield b"row\n"

        return StreamingResponse(body(), media_type="text/csv")

    @application.post("/background")
    def background() -> JSONResponse:
        session = get_db_session()
        seen["session"] = session
        marker = str(uuid.uuid4())
        seen["marker"] = marker

        def record() -> None:
            # Write after the response, as a disclosure audit row does.
            seen["pid"] = session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            session.execute(
                text("INSERT INTO probe_rows (marker) VALUES (:marker)"), {"marker": marker}
            )

        return JSONResponse({"ok": True}, background=BackgroundTask(record))

    return application, seen


def _backend_state(observer: Engine, pid: int) -> str | None:
    with observer.connect() as conn:
        return conn.execute(
            text("SELECT state FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid}
        ).scalar()


def test_streaming_body_that_reads_leaves_no_open_transaction(
    app: tuple[FastAPI, dict[str, Any]], observer: Engine
) -> None:
    application, seen = app
    with TestClient(application) as client:
        response = client.get("/stream")
    assert response.status_code == 200
    assert response.content == b"row\n"

    # The body really did reach the database, on a backend we can ask about.
    assert isinstance(seen.get("pid"), int)
    assert _backend_state(observer, seen["pid"]) != "idle in transaction"
    assert not seen["session"].in_transaction()


def test_background_task_write_is_committed_and_released(
    app: tuple[FastAPI, dict[str, Any]], observer: Engine
) -> None:
    application, seen = app
    with TestClient(application) as client:
        response = client.post("/background")
    assert response.status_code == 200

    assert isinstance(seen.get("pid"), int)
    assert _backend_state(observer, seen["pid"]) != "idle in transaction"
    assert not seen["session"].in_transaction()

    # And the write the background task made is durable, not stranded in a
    # transaction nobody will commit.
    with observer.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT count(*) FROM practice_after_response_probe.probe_rows"
                " WHERE marker = :marker"
            ),
            {"marker": seen["marker"]},
        ).scalar_one()
    assert stored == 1
