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
from typing import TYPE_CHECKING

import pytest
from tests.placeholder_db import database_url_is_placeholder

if TYPE_CHECKING:
    from collections.abc import Iterator

# Disable the Cloud Logging audit dual-write, exactly as ``tests/conftest.py``
# does for the unit suite — this file never got the same line, and every
# audited write in this suite paid for it. ``AuditService._persist`` calls
# ``write_to_cloud_logging`` whenever ``audit_dual_write_enabled`` is set,
# and that flag DEFAULTS TO TRUE.
#
# On CI there are no Application Default Credentials, so the client
# constructor raises immediately and the miss is invisible. On a developer
# machine with ADC present the client builds fine and ``log_struct`` makes a
# real network write to a real project — which is (a) a hang: the first
# audited test parks inside ``google.cloud.logging_v2.logger._do_log`` with
# the Postgres connection sitting ``idle in transaction``, taking the whole
# suite with it, and (b) wrong: synthetic audit rows land in the
# ``pablo.audit_events`` stream that the retention-locked GCS sink mirrors
# for six years.
#
# Module scope, not inside ``pytest_configure``: that function returns early
# when ``DATABASE_URL`` is already exported, and the flag has to be set
# before any app module is imported either way.
os.environ["AUDIT_DUAL_WRITE_ENABLED"] = "false"

# Same placement, same reason: before any app module is imported. ``Settings``
# is built once and cached, and outside ``development`` the app installs a
# trusted-host guard that answers 400 to the ``testserver`` Host every
# TestClient sends — so a suite that reaches settings first gets 400 on routes
# that need no auth at all, which reads as a broken route rather than a
# mis-set environment.
#
# ``tests/conftest.py`` has had this line all along; this file never did, and
# got away with it because the one module that cares
# (``api/test_dpop_e2e.py``) sets it at ITS import. That only holds while that
# module is the first to touch settings — an ordering nothing enforces, and
# which any new module importing ``app.*`` ahead of it quietly breaks.
# ``setdefault``, so an explicit environment still wins.
os.environ.setdefault("ENVIRONMENT", "development")

# And the other half of the same ordering problem. ``api/test_dpop_e2e.py``
# needs the middleware enforcing, and says so at its own import for want of
# anywhere better; when it loses the race the middleware waves every proof
# through and each of that module's rejection tests reports 200 where it wanted
# 401 — a failure that accuses the middleware of the one thing it did not do.
# Requests without ``X-Install-ID`` are untouched either way, so no other
# module here changes behaviour.
os.environ.setdefault("ENABLE_DPOP_VALIDATION", "true")


class _PgState:
    container = None  # type: ignore[var-annotated]


def pytest_configure(config: pytest.Config) -> None:
    """Bring up the Postgres container before app modules are imported."""
    # The unit suite plants a placeholder ``DATABASE_URL`` to get past settings
    # validation. Read as "a database is configured" it makes this function
    # stand down, ``DATABASE_BACKEND`` never becomes ``postgres``, and every
    # module here skips — so a combined run exits 0 having executed none of the
    # integration suite (PABLO-1vep). Refuse instead of skipping: a run that
    # cannot do what was asked should say so, not report success.
    if database_url_is_placeholder():
        raise pytest.UsageError(
            "The integration suite was started with the unit suite's placeholder "
            "DATABASE_URL, which is not a real database.\n\n"
            "This happens when both suites run in ONE pytest invocation "
            "(`pytest tests/ tests_integration/`): tests/conftest.py is imported "
            "first and plants the placeholder, so this conftest would stand down "
            "and every integration module would skip — reporting success without "
            "running anything.\n\n"
            "Run them as separate invocations (`make test-all` now does), or "
            "export a real DATABASE_URL to use your own database."
        )

    if os.environ.get("DATABASE_URL"):
        # The bring-your-own-database path, and the SECOND door onto the same
        # silent skip. Every module here is guarded by
        # ``skipif(... DATABASE_BACKEND != "postgres")``, and that variable used
        # to be set ONLY on the testcontainers path below. So exporting a real
        # DATABASE_URL — the workflow this module's docstring advertises —
        # collected the whole suite and skipped every test of it, exit 0.
        #
        # The caller supplied a Postgres URL; say so. ``setdefault`` leaves an
        # explicit DATABASE_BACKEND alone.
        os.environ.setdefault("DATABASE_BACKEND", "postgres")
        return

    # Disable the Ryuk reaper container — it mounts the host docker
    # socket, which Docker Desktop on macOS rejects with EINVAL on the
    # user-namespace socket. ``pytest_unconfigure`` stops the container
    # explicitly, so cleanup is covered without Ryuk. Must be set
    # before testcontainers is imported.
    #
    # The cost of no reaper: a run that is KILLED never reaches
    # ``pytest_unconfigure``, so its Postgres container survives. They
    # accumulate silently — several of them will quietly starve the
    # machine and make every later run slower and flakier, which reads as
    # "the suite got slow" rather than "I left six databases running".
    # After killing a run, ``docker ps`` and remove the stray
    # ``postgres:16-alpine``.
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


def clear_fastapi_dependency_caches() -> None:
    """Clear fastapi's module-level dependency-classification caches.

    fastapi classifies every dependency callable (generator? async
    generator? coroutine?) through a handful of ``lru_cache``-wrapped
    helpers in ``fastapi.dependencies.models``, keyed on the callable
    itself. Each app built in this suite defines its own generator
    session dependency closing over a fresh engine, so those caches
    accumulate a strong reference to every engine the suite has ever
    built and never let go — across hundreds of app constructions that
    exhausts the Postgres connection pool.

    Discover cache-bearing attributes by scanning the module rather than
    naming the three private helpers: they're internal, and upstream has
    already renamed the cache-size constant once.
    """
    from fastapi.dependencies import models  # noqa: PLC0415

    for name in dir(models):
        candidate = getattr(models, name)
        if hasattr(candidate, "cache_clear"):
            candidate.cache_clear()


@pytest.fixture(autouse=True)
def _release_fastapi_dependency_caches() -> Iterator[None]:
    """Release engines pinned by fastapi's dependency-classification caches.

    Runs after every test so each freshly-built app's dependency
    closures are eligible for garbage collection before the next one is
    constructed. See ``clear_fastapi_dependency_caches`` for why this is
    necessary.
    """
    yield
    clear_fastapi_dependency_caches()


@pytest.fixture(autouse=True)
def _isolate_rls_principal() -> Iterator[None]:
    """Put the tenant and principal ContextVars back the way the test found them.

    ``set_tenant_schema`` and ``arm_current_user_id`` stash the practice schema
    and the clinician on ContextVars that the pool-checkout listener re-applies
    to every connection it hands out. In the app that is exactly right and the
    request middleware clears them at the end of the request; here there is no
    middleware, so a test that arms a session leaves the next module's
    connections pointed at a schema it has never heard of — usually one this
    module has just dropped.

    It surfaces far from the cause: the symptom is an unrelated module writing
    a row that then cannot be read back, which reads as a bug in whatever it
    was testing. Cheaper to reset here, once, than to remember it nine times.
    """
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        _current_user_id,
    )

    tenant = _current_tenant_schema.get()
    user = _current_user_id.get()
    patient = _current_patient_id.get()
    try:
        yield
    finally:
        _current_tenant_schema.set(tenant)
        _current_user_id.set(user)
        _current_patient_id.set(patient)


@pytest.fixture
def test_user_id() -> str:
    """Default test user ID for integration tests."""
    return "integration-test-user-123"


@pytest.fixture
def test_user_id_2() -> str:
    """Second test user ID for multi-tenant tests."""
    return "integration-test-user-456"
