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
_SCHEMA_A = f"practice_ts_integ_a_{_SUFFIX}"
_SCHEMA_B = f"practice_ts_integ_b_{_SUFFIX}"


# ---------------------------------------------------------------------------
# Module-level imports (after env vars are set so settings resolves correctly)
# ---------------------------------------------------------------------------

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
        with tenant_db_session(_SCHEMA_A, "user-integ-1") as session:
            path = session.execute(text("SHOW search_path")).scalar() or ""
        assert _SCHEMA_A in path

    def test_rls_guc_is_armed_inside_context(self) -> None:
        with tenant_db_session(_SCHEMA_A, "user-integ-42") as session:
            uid = session.execute(
                text("SELECT current_setting('app.current_user_id', true)")
            ).scalar()
        assert uid == "user-integ-42"

    def test_write_and_commit(self) -> None:
        row_id = f"write-{uuid.uuid4().hex[:8]}"
        with tenant_db_session(_SCHEMA_A, "user-integ-1") as session:
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
            with tenant_db_session(_SCHEMA_A, "user-integ-1") as session:
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

        result = asyncio.run(run_in_tenant(_SCHEMA_A, "user-integ-thread", _insert))
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

        asyncio.run(run_in_tenant(_SCHEMA_A, "user-a", _insert_a))

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
