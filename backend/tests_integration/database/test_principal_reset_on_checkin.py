# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""A principal set on a pooled connection does not survive its checkin.

Application code arms ``app.current_user_id`` and ``app.current_patient_id``
transaction-locally, so they normally end with the transaction. A
session-level ``set_config(..., false)`` does not: without the pool's checkin
reset it rides the physical connection into whichever checkout draws it next,
and that checkout starts out reading as somebody. The integration suite once
did exactly this, and later modules started armed as a user from another one.

The engine here has ``pool_size=1, max_overflow=0``, so every checkout reuses
one physical connection, and ``pg_backend_pid()`` is asserted equal across
the two checkouts so the test cannot pass by being handed a fresh one. The
checkin listener under test is the production one: it is registered on every
``Engine``, this one included.

Run: ``make test-integration``.
"""

from __future__ import annotations

import contextvars
import os
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_PRINCIPALS = ("app.current_user_id", "app.current_patient_id")
_SOMEBODY = "5b0c1d7e-2f4a-4c3b-9e8d-7a6f5e4d3c2b"


@pytest.fixture
def pooled() -> Iterator[Engine]:
    """One physical connection, reused by every checkout."""
    eng = create_engine(_DB_URL, pool_size=1, max_overflow=0, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _pid(conn: Connection) -> int:
    return int(conn.execute(text("SELECT pg_backend_pid()")).scalar_one())


@pytest.mark.parametrize("principal", _PRINCIPALS)
def test_a_session_level_principal_does_not_survive_checkin(pooled: Engine, principal: str) -> None:
    with pooled.connect() as conn:
        first_pid = _pid(conn)
        conn.execute(
            text("SELECT set_config(:setting, :value, false)"),
            {"setting": principal, "value": _SOMEBODY},
        )
        conn.commit()
        # Session-level: it outlives the commit on this connection.
        assert (
            conn.execute(
                text("SELECT current_setting(:setting, true)"), {"setting": principal}
            ).scalar_one()
            == _SOMEBODY
        )

    with pooled.connect() as conn:
        assert _pid(conn) == first_pid, "the pool handed out a different connection"
        after = conn.execute(
            text("SELECT current_setting(:setting, true)"), {"setting": principal}
        ).scalar_one()

    assert not after, f"{principal} survived checkin as {after!r}"


def test_both_principals_are_cleared_together(pooled: Engine) -> None:
    """A connection carrying both comes back carrying neither."""
    with pooled.connect() as conn:
        first_pid = _pid(conn)
        for principal in _PRINCIPALS:
            conn.execute(
                text("SELECT set_config(:setting, :value, false)"),
                {"setting": principal, "value": _SOMEBODY},
            )
        conn.commit()

    def next_request() -> tuple[int, dict[str, str | None], str]:
        with pooled.connect() as conn:
            values = {
                principal: conn.execute(
                    text("SELECT current_setting(:setting, true)"), {"setting": principal}
                ).scalar_one()
                for principal in _PRINCIPALS
            }
            return _pid(conn), values, conn.execute(text("SHOW search_path")).scalar_one()

    # In an empty context, as the next request starts: the checkout listener
    # re-applies a tenant ``search_path`` from a request ContextVar, and one
    # left behind by an earlier test would otherwise be what is measured.
    pid, values, search_path = contextvars.Context().run(next_request)

    assert pid == first_pid
    assert not any(values.values()), f"principals survived checkin: {values}"
    assert search_path == "platform, public"


def test_a_connection_never_armed_checks_in_cleanly(pooled: Engine) -> None:
    """Resetting a principal that was never set is harmless: the connection
    still checks in, and comes back usable and on the same backend."""
    with pooled.connect() as conn:
        first_pid = _pid(conn)

    with pooled.connect() as conn:
        assert _pid(conn) == first_pid
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
