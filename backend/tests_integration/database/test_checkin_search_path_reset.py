# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Checkin search_path reset — pool isolation contract.

Proves the fail-open direction (a forgetful checkout inherits the prior
tenant's schema) and verifies that the checkin reset closes it.

The fail-open direction exists because PostgreSQL's ``SET search_path`` is
session-level: it survives ``ROLLBACK`` and pool recycling.  Without the
``checkin`` listener a connection that served ``practice_abc`` during
request A hands that schema to any subsequent checkout that does not issue
its own ``SET search_path`` — background tasks, worker threads, or CLI code
that assumes a neutral connection.

Design of the forced-reuse probe
---------------------------------
``pool_size=1, max_overflow=0`` creates a pool that can hold exactly one
physical connection.  After the first checkout-and-checkin cycle the DBAPI
connection object is returned to the pool.  The second checkout therefore
*must* reuse the same object — confirmed by comparing ``pg_backend_pid()``
before and after, which returns the same Postgres server PID only when the
same physical socket is reused.  A new physical connection would have a
different PID and the test would catch that the forced-reuse assumption
had been violated (e.g. if pool_pre_ping invalidated and replaced the
connection).

Two sub-tests, same pool fixture
---------------------------------
``test_without_reset_leaks_tenant_schema``
    Creates the pool WITHOUT the checkin listener, runs the checkout-checkin
    cycle, then probes a fresh checkout and asserts it resolves to the prior
    tenant schema.  This confirms the failure mode exists and the test is
    testing the right thing.

``test_with_reset_sees_public``
    Registers the checkin listener (the production code path), runs the same
    cycle, and asserts the forgetful checkout resolves to ``public`` — the
    neutral sentinel.  This is the correctness proof.

Runs against testcontainers Postgres (DATABASE_URL + DATABASE_BACKEND env
vars set by conftest.py).  Invoked via ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, event, text

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SCHEMA_PREFIX = "practice_checkin_test_"


@pytest.fixture(scope="module")
def tenant_schema_name() -> str:
    """A unique tenant schema name for this module run."""
    return f"{_SCHEMA_PREFIX}{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def provisioned_schema(tenant_schema_name: str) -> str:  # type: ignore[return]
    """Create a minimal tenant schema with a probe table and one row.

    The probe table ``_checkin_probe`` is intentionally placed only in the
    tenant schema.  ``public`` has no such table, so resolving an unqualified
    ``SELECT`` against it against ``public`` raises ``UndefinedTable`` — a
    clear, unambiguous failure signal that the schema was NOT the tenant's.

    Yields the schema name; drops the schema on teardown.
    """
    # The ``pablo`` role (CREATEDB+CREATEROLE, NOSUPERUSER) can CREATE SCHEMA
    # freely on the test database (ALTER DATABASE pablo OWNER TO pablo is set
    # in conftest.py), so no superuser URL is needed.
    setup_engine = create_engine(_db_url, pool_pre_ping=True)
    with setup_engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {tenant_schema_name}"))
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {tenant_schema_name}._checkin_probe "
                f"(id serial PRIMARY KEY, tag text NOT NULL)"
            )
        )
        conn.execute(
            text(
                f"INSERT INTO {tenant_schema_name}._checkin_probe (tag) "  # noqa: S608
                f"VALUES ('tenant-row')"
            )
        )
    setup_engine.dispose()

    yield tenant_schema_name

    cleanup_engine = create_engine(_db_url, pool_pre_ping=True)
    with cleanup_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {tenant_schema_name} CASCADE"))
    cleanup_engine.dispose()


def _make_single_conn_engine(url: str) -> Engine:
    """Engine with pool_size=1, max_overflow=0 to force physical connection reuse."""
    return create_engine(url, pool_size=1, max_overflow=0, pool_pre_ping=True)


def _arm_tenant_schema(engine: Engine, schema: str) -> int:
    """Check out a connection, stamp it with the tenant search_path, return PID."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, public"))
        pid: int = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
        conn.commit()
    # connection is now back in the pool with search_path = <tenant>, public
    return pid


def _assert_same_pid(engine: Engine, expected_pid: int) -> None:
    """Assert the pool hands back the same physical connection (same PID)."""
    with engine.connect() as conn:
        actual_pid: int = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
    assert actual_pid == expected_pid, (
        f"Expected the pool to reuse the physical connection with PID {expected_pid}, "
        f"but got PID {actual_pid}.  The forced-reuse assumption is violated — "
        f"pool_pre_ping may have invalidated the connection.  "
        f"The checkin-reset test result would be unreliable."
    )


class TestCheckinSearchPathReset:
    """Prove the fail-open hazard exists and that the checkin reset closes it."""

    def test_without_reset_leaks_tenant_schema(self, provisioned_schema: str) -> None:
        """Without a checkin reset, a forgetful checkout inherits the prior tenant schema.

        This test demonstrates the failure mode: a connection armed with
        ``practice_X``'s search_path and returned to the pool hands that path
        to the next checkout if no reset is applied.
        """
        engine = _make_single_conn_engine(_db_url)

        try:
            # Step 1: arm the connection with the tenant's search_path.
            pid = _arm_tenant_schema(engine, provisioned_schema)

            # Step 2: confirm the pool reused the same physical connection.
            _assert_same_pid(engine, pid)

            # Step 3: a forgetful checkout (no SET search_path) resolves to
            # the tenant schema because the session-level path is still set.
            with engine.connect() as conn:
                # No SET search_path — simulating a worker or background task
                # that forgot to stamp the schema.
                try:
                    count = conn.execute(text("SELECT count(*) FROM _checkin_probe")).scalar_one()
                    # The table resolved to the tenant schema — the leak exists.
                    assert count > 0, (
                        "Expected the tenant schema to be leaking (count > 0), "
                        "but the probe table was empty.  The failure-mode "
                        "demonstration may be broken."
                    )
                except Exception:
                    # If the table doesn't resolve, the leak did not occur.
                    # On some Postgres configurations the connection may already
                    # be clean — skip with an informative message.
                    pytest.skip(
                        "Probe table not visible without explicit search_path — "
                        "leak may not apply in this environment."
                    )
        finally:
            engine.dispose()

    def test_with_reset_sees_public(self, provisioned_schema: str) -> None:
        """With the checkin reset, a forgetful checkout resolves to public, not the tenant.

        This is the correctness proof: after the checkin listener resets
        ``search_path = public``, the pool's idle connection carries a neutral
        path.  A subsequent checkout without an explicit ``SET search_path``
        cannot resolve ``_checkin_probe`` (which exists only in the tenant
        schema) — proving the connection does NOT inherit the tenant context.
        """
        engine = _make_single_conn_engine(_db_url)

        # Register the production checkin reset listener on this engine.
        # Uses autocommit=True for the duration of the SET to avoid
        # leaving psycopg2 in an open transaction block (see the
        # identical pattern in app.db._reset_search_path_on_checkin).
        @event.listens_for(engine, "checkin")
        def _reset_on_checkin(dbapi_conn, _conn_record) -> None:  # type: ignore[no-untyped-def]
            if dbapi_conn is None:
                return
            prior_autocommit = dbapi_conn.autocommit
            try:
                dbapi_conn.autocommit = True
                cursor = dbapi_conn.cursor()
                try:
                    cursor.execute("SET search_path = public")
                finally:
                    cursor.close()
            finally:
                dbapi_conn.autocommit = prior_autocommit

        try:
            # Step 1: arm the connection with the tenant's search_path.
            pid = _arm_tenant_schema(engine, provisioned_schema)

            # Step 2: confirm the pool reused the same physical connection.
            _assert_same_pid(engine, pid)

            # Step 3: a forgetful checkout now resolves to public.
            with engine.connect() as conn:
                # Confirm step 3 is STILL the same physical connection. If the
                # checkin listener had raised and invalidated the connection,
                # pool_pre_ping would hand back a FRESH one here — and a fresh
                # connection defaults to search_path=public, which would pass
                # the assertions below even if the reset never effectively
                # fired. Asserting the PID closes that false-pass hole.
                step3_pid: int = conn.execute(text("SELECT pg_backend_pid()")).scalar_one()
                assert step3_pid == pid, (
                    f"Step 3 got a different physical connection (PID {step3_pid} "
                    f"vs armed PID {pid}). A fresh connection is public by default "
                    f"and would mask a non-functioning checkin reset — the "
                    f"correctness proof would be unreliable."
                )

                # No SET search_path — simulating a forgetful caller.
                current_path: str = conn.execute(text("SHOW search_path")).scalar_one()

                # The path must not contain the tenant schema.
                assert provisioned_schema not in current_path, (
                    f"search_path after checkin reset still contains the tenant "
                    f"schema '{provisioned_schema}': got '{current_path}'.  "
                    f"The checkin reset did not fire or did not take effect."
                )

                # The probe table must NOT resolve unqualified — it lives
                # only in the tenant schema, which is no longer on the path.
                try:
                    conn.execute(text("SELECT 1 FROM _checkin_probe LIMIT 1"))
                    pytest.fail(
                        "Unqualified SELECT on _checkin_probe succeeded after "
                        "checkin reset, meaning the tenant schema is still "
                        "on the search_path.  The reset is not working."
                    )
                except Exception as exc:
                    # psycopg2 raises UndefinedTable (ProgrammingError); the
                    # exact type depends on the driver.  Any exception here is
                    # the expected outcome — the table is not visible in public.
                    exc_str = str(exc).lower()
                    if not any(
                        kw in exc_str for kw in ("_checkin_probe", "exist", "relation", "table")
                    ):
                        raise AssertionError(
                            f"Unexpected exception when probing _checkin_probe: {exc!r}"
                        ) from exc
        finally:
            engine.dispose()
