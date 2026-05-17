"""Shared fixtures for integration tests.

Provides PostgreSQL setup and other integration test utilities.

If ``DATABASE_URL`` is already exported (the historical
``make db-up && make db-migrate`` workflow), the bootstrap is a no-op.
Otherwise a disposable Postgres is brought up via testcontainers and
the matching env vars are exported BEFORE any test module is collected
— ``app.settings.get_settings`` is ``lru_cache``'d at module import,
so the env var must be set before app code is imported.
"""

from __future__ import annotations

import os

import pytest


class _PgState:
    container = None  # type: ignore[var-annotated]


def pytest_configure(config: pytest.Config) -> None:
    """Bring up the Postgres container before app modules are imported."""
    if os.environ.get("DATABASE_URL"):
        return

    # Disable the Ryuk reaper container — it mounts the host docker
    # socket, which Docker Desktop on macOS rejects with EINVAL on the
    # user-namespace socket. ``pytest_unconfigure`` stops the container
    # explicitly, so cleanup is covered without Ryuk. Must be set
    # before testcontainers is imported.
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

    try:
        from testcontainers.postgres import PostgresContainer  # noqa: PLC0415
    except ImportError:
        # Tests that need Postgres will fail with a clear KeyError on
        # DATABASE_URL — preferable to a confusing skipif chain.
        return

    # Pin to docker-compose's major version so tests match dev. driver=None
    # yields the bare ``postgresql://`` URL SQLAlchemy expects.
    #
    # IMPORTANT: bootstrap as ``postgres`` (not ``pablo``) so we can
    # later create ``pablo`` as a *non-superuser* role. PostgreSQL
    # refuses ``ALTER ROLE … NOSUPERUSER`` against the bootstrap user
    # ("The bootstrap user must have the SUPERUSER attribute"), so any
    # role created via ``POSTGRES_USER`` is permanently a superuser
    # for the container's lifetime. Production's ``pablo`` is *not* a
    # superuser (``rolsuper=false, rolbypassrls=false`` in
    # pablohealth-oss), and a superuser bypasses every RLS policy —
    # including FORCE ROW LEVEL SECURITY — which silently turns the
    # integration suite into a no-RLS suite. Today's pentest finding
    # (PABLO-API-500) lived in exactly the gap that a superuser test
    # role papers over.
    _PgState.container = PostgresContainer(
        "postgres:16-alpine",
        username="postgres",
        password="postgres_dev",  # noqa: S106 — ephemeral test container, not a secret
        dbname="pablo",
        driver=None,
    )
    _PgState.container.start()

    # Provision the ``pablo`` role as a normal user (CREATEDB +
    # CREATEROLE so alembic and provisioning still work, but
    # NOSUPERUSER + NOBYPASSRLS so RLS policies actually apply).
    # Grant ownership of the ``pablo`` database so migrations can
    # CREATE SCHEMA / CREATE TABLE freely.
    import psycopg2  # noqa: PLC0415

    bootstrap_url = _PgState.container.get_connection_url()
    conn = psycopg2.connect(bootstrap_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "CREATE ROLE pablo WITH LOGIN PASSWORD 'pablo_dev' "
            "CREATEDB CREATEROLE NOSUPERUSER NOBYPASSRLS"
        )
        cur.execute("ALTER DATABASE pablo OWNER TO pablo")
        # ``pablo`` needs CREATE on the database to make new schemas
        # and on ``public`` so existing public-schema objects work.
        cur.execute("GRANT ALL ON SCHEMA public TO pablo")
    conn.close()

    # Advertise the pablo-scoped URL so app + tests use the non-super
    # role. Replace ``postgres:postgres_dev@`` with ``pablo:pablo_dev@``.
    pablo_url = bootstrap_url.replace("postgres:postgres_dev@", "pablo:pablo_dev@", 1)
    os.environ["DATABASE_URL"] = pablo_url
    os.environ["DATABASE_BACKEND"] = "postgres"


def pytest_unconfigure(config: pytest.Config) -> None:
    if _PgState.container is not None:
        _PgState.container.stop()


@pytest.fixture
def test_user_id() -> str:
    """Default test user ID for integration tests."""
    return "integration-test-user-123"


@pytest.fixture
def test_user_id_2() -> str:
    """Second test user ID for multi-tenant tests."""
    return "integration-test-user-456"
