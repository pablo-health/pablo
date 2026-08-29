# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration tests for app.db.tenant_session with a real PostgreSQL instance.

Verifies that:
* ``tenant_db_session`` opens a proper tenant-scoped session (correct
  search_path, RLS GUC set), commits on clean exit, and rolls back on error.
* ``run_in_tenant`` dispatches to a worker thread and delivers a
  tenant-scoped session inside it.
* Data written in one tenant schema is invisible to a second schema,
  confirming that the search_path routing is correct end-to-end.

Runs only when DATABASE_URL and DATABASE_BACKEND=postgres are set.
The conftest.py in tests_integration/ provisions those via testcontainers
when they are absent.

IMPORTANT: ``MULTI_TENANCY_ENABLED`` is set to ``false`` for this entire
module so the ``assert_tenant_schema_set`` guard (which checks the schema
name against the default) does not block explicit-schema writes in the
integration canary tables.  The guard logic is tested separately in the
unit tests (test_tenant_session.py) using mocks.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine, text

# Skip the whole module if no Postgres URL is available.
_DB_URL = os.environ.get("DATABASE_URL", "")
_SKIP = not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres"
pytestmark = pytest.mark.skipif(
    _SKIP,
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

# Disable the fail-closed guard so writes to the explicit canary schemas
# are not blocked.  The guard behaviour is unit-tested with mocks in
# tests/test_tenant_session.py.
os.environ.setdefault("MULTI_TENANCY_ENABLED", "false")

_SUFFIX = uuid.uuid4().hex[:8]
# Caller user_ids are native uuid columns; readable names below.
_USER_A = "bb8e799c-a57b-533d-a8c4-6648b68eed2d"
_USER_1 = "4bd6452f-45bf-53d0-9680-693205fde295"
_USER_42 = "bbf9973e-6f8a-5325-9f9d-210ce500493a"
_USER_THREAD = "829636a5-8c15-58e3-b807-f34780edf78d"
_SCHEMA_A = f"practice_ts_integ_a_{_SUFFIX}"
_SCHEMA_B = f"practice_ts_integ_b_{_SUFFIX}"


# ---------------------------------------------------------------------------
# Module-level imports (after env vars are set so settings resolves correctly)
# ---------------------------------------------------------------------------

from app.db import (  # noqa: E402
    _request_session,
    assert_no_held_db_connection,
    bound_db_sessions,
    create_standalone_session,
)
from app.db.tenant_session import run_in_tenant, tenant_db_session  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _engine():  # type: ignore[return]
    return create_engine(_DB_URL, pool_pre_ping=True)


@pytest.fixture(scope="module")
def _schemas(_engine):  # type: ignore[return]
    """Create minimal tenant schemas with a ``canary`` table for write tests."""
    with _engine.connect() as conn:
        for schema in (_SCHEMA_A, _SCHEMA_B):
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            conn.execute(
                text(f"""
                    CREATE TABLE IF NOT EXISTS {schema}.canary (
                        id VARCHAR(64) PRIMARY KEY,
                        label VARCHAR(128) NOT NULL
                    )
                """)
            )
        conn.commit()
    yield
    with _engine.connect() as conn:
        for schema in (_SCHEMA_A, _SCHEMA_B):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.commit()


# ---------------------------------------------------------------------------
# tenant_db_session integration tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_schemas")
class TestTenantDbSessionIntegration:
    def test_search_path_is_set_to_practice_schema(self) -> None:
        """The session must report the correct search_path."""
        with tenant_db_session(_SCHEMA_A, _USER_1) as session:
            path = session.execute(text("SHOW search_path")).scalar() or ""
        assert _SCHEMA_A in path

    def test_rls_guc_is_armed_inside_context(self) -> None:
        with tenant_db_session(_SCHEMA_A, _USER_42) as session:
            uid = session.execute(
                text("SELECT current_setting('app.current_user_id', true)")
            ).scalar()
        assert uid == _USER_42

    def test_write_and_commit(self) -> None:
        row_id = f"write-{uuid.uuid4().hex[:8]}"
        with tenant_db_session(_SCHEMA_A, _USER_1) as session:
            session.execute(
                text(f"INSERT INTO {_SCHEMA_A}.canary (id, label) VALUES (:id, :label)"),  # noqa: S608
                {"id": row_id, "label": "test-write"},
            )

        # Verify the row persisted using a raw connection outside the context.
        with create_engine(_DB_URL).connect() as raw:
            count = raw.execute(
                text(f"SELECT COUNT(*) FROM {_SCHEMA_A}.canary WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            ).scalar()
        assert count == 1

    def test_rollback_on_exception(self) -> None:
        row_id = f"rollback-{uuid.uuid4().hex[:8]}"

        def _body() -> None:
            with tenant_db_session(_SCHEMA_A, _USER_1) as session:
                session.execute(
                    text(
                        f"INSERT INTO {_SCHEMA_A}.canary (id, label) VALUES (:id, :label)"  # noqa: S608
                    ),
                    {"id": row_id, "label": "should-not-persist"},
                )
                raise RuntimeError("intentional")

        with pytest.raises(RuntimeError, match="intentional"):
            _body()

        with create_engine(_DB_URL).connect() as raw:
            count = raw.execute(
                text(f"SELECT COUNT(*) FROM {_SCHEMA_A}.canary WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            ).scalar()
        assert count == 0


# ---------------------------------------------------------------------------
# run_in_tenant integration tests
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_schemas")
class TestRunInTenantIntegration:
    def test_write_via_worker_thread_committed(self) -> None:
        row_id = f"thread-{uuid.uuid4().hex[:8]}"

        def _insert(session: Any) -> str:
            session.execute(
                text(
                    f"INSERT INTO {_SCHEMA_A}.canary (id, label) VALUES (:id, :label)"  # noqa: S608
                ),
                {"id": row_id, "label": "thread-write"},
            )
            return row_id

        result = asyncio.run(run_in_tenant(_SCHEMA_A, _USER_THREAD, _insert))
        assert result == row_id

        with create_engine(_DB_URL).connect() as raw:
            count = raw.execute(
                text(f"SELECT COUNT(*) FROM {_SCHEMA_A}.canary WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            ).scalar()
        assert count == 1

    def test_schema_isolation_across_two_tenants(self) -> None:
        """Rows written to schema A must not appear in schema B."""
        row_id = f"isolate-{uuid.uuid4().hex[:8]}"

        def _insert_a(session: Any) -> None:
            session.execute(
                text(
                    f"INSERT INTO {_SCHEMA_A}.canary (id, label) VALUES (:id, :label)"  # noqa: S608
                ),
                {"id": row_id, "label": "schema-a-only"},
            )

        asyncio.run(run_in_tenant(_SCHEMA_A, _USER_A, _insert_a))

        with create_engine(_DB_URL).connect() as raw:
            count_a = raw.execute(
                text(f"SELECT COUNT(*) FROM {_SCHEMA_A}.canary WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            ).scalar()
            count_b = raw.execute(
                text(f"SELECT COUNT(*) FROM {_SCHEMA_B}.canary WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            ).scalar()
        assert count_a == 1
        assert count_b == 0


@pytest.mark.usefixtures("_schemas")
class TestDisplacedSessionIsReleased:
    """A published session must not strand the one it displaces.

    ``DatabaseSessionMiddleware`` opens its session's transaction at request
    entry (its ``SET search_path``) and does not touch it again until teardown.
    When a route opens its own session and publishes it, nothing reaches the
    middleware's session for the whole unit of work — so it sits idle in that
    transaction, Postgres terminates the backend at
    ``idle_in_transaction_session_timeout``, and the teardown commit fails on a
    closed socket long after the route's work succeeded. The request reports a
    5xx for work that completed, which on a task-queue target is retried.

    These use a real session against real Postgres rather than mocks, because
    what is being asserted is that a connection genuinely went back to the pool.
    Both fail without the release in ``publish_request_session``.
    """

    def test_outer_session_is_not_left_in_a_transaction(self) -> None:
        # Stand in for the middleware: a session whose transaction is already
        # open from its entry SET search_path.
        outer = create_standalone_session(_SCHEMA_A)
        token = _request_session.set(outer)
        try:
            assert outer.in_transaction(), "precondition: the outer session holds a connection"

            with tenant_db_session(_SCHEMA_B, _USER_1):
                # However long the body runs — a Gmail sync, an LLM call, a
                # multi-minute poll — the displaced session must not be holding
                # a transaction while it does.
                assert not outer.in_transaction()

            assert not outer.in_transaction()
            # And it is still usable afterwards: releasing is a commit, not a
            # close. The next query auto-begins a fresh, re-armed transaction.
            path = outer.execute(text("SHOW search_path")).scalar() or ""
            assert _SCHEMA_A in path
        finally:
            outer.close()
            _request_session.reset(token)

    def test_no_bound_session_holds_a_connection_at_an_external_call_seam(self) -> None:
        """What a caller asserts before slow I/O: nothing is holding a connection.

        Asserted over ``bound_db_sessions()`` rather than through
        ``assert_no_held_db_connection`` alone, because that guard only LOGS
        when settings resolve as production — which they do in some
        environments, and a test that silently stops asserting is worse than no
        test. The guard is exercised for its raise/log behaviour in
        ``tests/test_assert_no_held_db_connection.py`` with settings patched.
        """
        outer = create_standalone_session(_SCHEMA_A)
        token = _request_session.set(outer)
        try:
            with tenant_db_session(_SCHEMA_B, _USER_1) as inner:
                inner.commit()  # the engine's own per-call discipline
                held = [s for s in bound_db_sessions() if s.in_transaction()]
                assert held == []
                assert outer in bound_db_sessions(), "the displaced session is still tracked"
                assert_no_held_db_connection("integration-external-call")
        finally:
            outer.close()
            _request_session.reset(token)
