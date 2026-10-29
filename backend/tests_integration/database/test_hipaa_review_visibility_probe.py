# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The log review tells a table it cannot see from a table with nothing in it.

``audit_rows_hidden`` is what stops a review running as the wrong role from
reporting a clean, quiet day. It compares what the connection can read with
the server's table statistics, which row-level security does not filter.

Run under the integration ``pablo`` role, which the conftest creates
NOSUPERUSER NOBYPASSRLS as production has it, against schemas built by the
real ``create_practice_schema``, so the row policies are the ones that ship.

The three cases, each with a control on the same connection so none can pass
vacuously:

1. Rows exist and the connection's policies hide all of them: hidden.
2. The same rows, read by a principal the policies let see them: not hidden.
3. No rows at all: not hidden. A quiet practice is not a blind review.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_ACTOR = str(uuid.uuid4())


def _arm(conn: Connection, schema: str, *, user_id: str = "") -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
    conn.execute(text("SELECT set_config('app.current_patient_id', '', false)"))


def _live_rows(conn: Connection, schema: str) -> int:
    value = conn.execute(
        text(
            "SELECT n_live_tup FROM pg_stat_user_tables"
            " WHERE schemaname = :s AND relname = 'audit_logs'"
        ),
        {"s": schema},
    ).scalar()
    return int(value or 0)


@pytest.fixture(scope="module")
def schemas(engine: Engine) -> Iterator[tuple[str, str]]:
    """One practice with an audit row in it, and one with none."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    busy = f"practice_test_probe_busy_{uuid.uuid4().hex[:8]}"
    quiet = f"practice_test_probe_quiet_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, busy)
    create_practice_schema(engine, quiet)

    with engine.begin() as conn:
        _arm(conn, busy, user_id=_ACTOR)
        conn.execute(
            text(
                'INSERT INTO audit_logs (id, "timestamp", expires_at, user_id, actor_type,'
                " action, resource_type, resource_id)"
                " VALUES (CAST(:id AS uuid), :ts, :exp, :u, 'user',"
                "         'patient_list_viewed', 'patient', :u)"
            ),
            {
                "id": str(uuid.uuid4()),
                "ts": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(days=2555),
                "u": _ACTOR,
            },
        )
        _arm(conn, busy)

    # Table statistics reach shared memory shortly after the writing
    # transaction ends, not at commit. Wait rather than race them.
    with engine.connect() as conn:
        deadline = time.monotonic() + 15
        while _live_rows(conn, busy) == 0 and time.monotonic() < deadline:
            conn.rollback()
            time.sleep(0.25)
        assert _live_rows(conn, busy) == 1, "statistics never counted the inserted row"

    yield busy, quiet

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {busy} CASCADE"))
        conn.execute(text(f"DROP SCHEMA IF EXISTS {quiet} CASCADE"))


def test_the_role_under_test_cannot_bypass_row_policies(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
    assert row == (False, False)


def test_rows_the_policies_hide_are_reported_hidden(
    engine: Engine, schemas: tuple[str, str]
) -> None:
    from app.jobs.hipaa_log_review import audit_rows_hidden  # noqa: PLC0415

    busy, _ = schemas
    with engine.connect() as conn:
        _arm(conn, busy)
        assert conn.execute(text("SELECT count(*) FROM audit_logs")).scalar() == 0
        assert _live_rows(conn, busy) == 1
        assert audit_rows_hidden(conn, busy) is True


def test_the_same_rows_seen_by_their_writer_are_not_hidden(
    engine: Engine, schemas: tuple[str, str]
) -> None:
    from app.jobs.hipaa_log_review import audit_rows_hidden  # noqa: PLC0415

    busy, _ = schemas
    with engine.connect() as conn:
        _arm(conn, busy, user_id=_ACTOR)
        assert conn.execute(text("SELECT count(*) FROM audit_logs")).scalar() == 1
        assert audit_rows_hidden(conn, busy) is False


def test_an_empty_table_is_quiet_not_hidden(engine: Engine, schemas: tuple[str, str]) -> None:
    from app.jobs.hipaa_log_review import audit_rows_hidden  # noqa: PLC0415

    busy, quiet = schemas
    with engine.connect() as conn:
        # Control: the busy schema, read the same way, IS hidden, so a False
        # below is about the empty table, not a probe that never says True.
        _arm(conn, busy)
        assert audit_rows_hidden(conn, busy) is True
        _arm(conn, quiet)
        assert _live_rows(conn, quiet) == 0
        assert audit_rows_hidden(conn, quiet) is False
