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
    # yields the bare ``postgresql://`` URL SQLAlchemy expects. Username
    # must be ``pablo`` so migration ``e8f2a9c1b043`` can ``GRANT SELECT
    # ... TO pablo`` without first creating the role.
    _PgState.container = PostgresContainer(
        "postgres:16-alpine",
        username="pablo",
        password="pablo_dev",  # noqa: S106 — ephemeral test container, not a secret
        dbname="pablo",
        driver=None,
    )
    _PgState.container.start()
    os.environ["DATABASE_URL"] = _PgState.container.get_connection_url()
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
