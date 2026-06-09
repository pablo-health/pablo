# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end: ``POST /api/patients`` against a real Postgres + RLS tenant.

Regression for the 2026-05-17 pentest finding (PABLO-API-500). The
automated pentest run consistently reproduced a 500 on
``POST /api/patients`` against a freshly-provisioned
``practice_pentest_*`` schema. Cloud Run logs showed a
``ForeignKeyViolation`` on ``patient_clinicians_patient_id_fkey`` —
the grant row's INSERT could not see the patient row that was added
to the same SQLAlchemy session moments earlier.

The unit suite at ``backend/tests/test_patients_api.py`` cannot catch
this class of bug: its conftest patches ``app.db.get_engine`` and
``app.db.get_session_factory`` with ``MagicMock``, so no SQL is ever
sent to a database, RLS is never evaluated, and FK constraints are
never enforced. "2 passed" there proves only that the route assembles
without raising.

What this test does instead:
  * Brings up Postgres via the testcontainers fixture in the parent
    ``conftest.py`` (or reuses ``DATABASE_URL`` if exported).
  * Runs ``alembic upgrade head`` so the ``has_patient_access``
    function and ``patient_clinicians`` table exist.
  * Provisions a fresh tenant schema using the **same** code path
    ``PentestTenantService.provision`` exercises:
    ``create_practice_schema(engine, schema)`` — which enables RLS
    via ``enable_rls_on_schema`` exactly as production does.
  * Drives the route through ``TestClient`` so middleware, auth
    dependencies, repository, and audit service all run for real.

If the pentest's PABLO-API-500 root cause is RLS-related, this test
will fail with the same 500. If the bug is specific to some other
piece of the ``/api/admin/pentest/tenant`` path (identity bootstrap,
allowlist seeding, etc.), this test will pass and we'll know to look
there instead.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

# Same pattern other integration tests use — ENVIRONMENT must be set
# before any ``app.*`` import.
os.environ.setdefault("ENVIRONMENT", "development")
# Enable multi-tenancy so DatabaseSessionMiddleware honors per-request
# schema resolution. With ``multi_tenancy_enabled=False`` (the default)
# the middleware short-circuits to ``DEFAULT_PRACTICE_SCHEMA`` and the
# monkey-patched schema resolver is never called — every INSERT lands
# in the ``practice`` template instead of the test tenant. Settings is
# lru_cached on first read, so this must be set before ``app.main`` is
# imported (which happens in the ``fastapi_app`` fixture below).
os.environ.setdefault("MULTI_TENANCY_ENABLED", "true")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """Engine bound to the integration Postgres + alembic head applied.

    ``has_patient_access`` and ``patient_clinicians`` come from
    migrations ``777b846ab944`` and ``c8a31f6e2d54`` — not from
    ``Base.metadata.create_all``, so ``ensure_schemas`` alone is
    insufficient. Run alembic explicitly here.
    """
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")

    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    """Provision a tenant schema the same way pentest provisioning does.

    Calls ``create_practice_schema`` directly — that's the function
    ``PentestTenantService.provision`` invokes. RLS is enabled via
    ``enable_rls_on_schema`` as a side effect, matching prod.

    Note: prior to running this we warm the pool with a connection
    that sets ``search_path = practice, platform, public``. The RLS
    policies created by ``enable_rls_on_schema`` call
    ``has_patient_access`` unqualified, and that function lives only
    in the ``practice`` schema. Without the warm-up the policy CREATE
    fails because the pooled connection comes up with the role
    default ``"$user", public``. The fragility is itself a bug —
    tracked separately — but reproducing the pentest's FK finding
    requires getting past the policy creation step the way prod does.
    """
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_e2e_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def fastapi_app() -> FastAPI:
    """Import the FastAPI app lazily, after pytestmark gate.

    ``app.main`` connects to ``DATABASE_URL`` at import time via
    ``ensure_schemas``.
    """
    from app.main import app  # noqa: PLC0415

    return app


@pytest.fixture
def e2e_user_id() -> str:
    # Stable across the module so the audit service and patient grant
    # use the same id.
    return "7b0f2f61-a44d-56fc-a4e2-5aa536f6dfc9"


@pytest.fixture
def e2e_user(e2e_user_id: str):
    from app.models import User  # noqa: PLC0415

    return User(
        id=e2e_user_id,
        email="e2e-patients@example.com",
        name="E2E Patients User",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def e2e_client(  # noqa: PLR0913 — fixture composition mirrors the FastAPI deps
    fastapi_app: FastAPI,
    engine: Engine,
    tenant_schema: str,
    e2e_user,
    e2e_user_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    """TestClient driving the real route stack into ``tenant_schema``.

    Wiring:
      * ``DatabaseSessionMiddleware`` opens a session per request and
        sets ``search_path``. We patch its schema resolver to return
        ``tenant_schema`` (no Firebase token to decode in tests).
      * Auth dependencies are overridden to return ``e2e_user`` — no
        Firebase round-trip, no MFA, no allowlist check.
      * ``get_tenant_context`` is overridden to set
        ``app.current_user_id`` on the request session, mirroring the
        production dependency on the same line in
        ``auth.service.get_tenant_context``.
    """
    from app.auth.service import (  # noqa: PLC0415
        TenantContext,
        get_current_user,
        get_current_user_id,
        get_current_user_no_mfa,
        get_tenant_context,
        require_active_subscription,
        require_baa_acceptance,
    )
    from app.db import get_db_session  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    monkeypatch.setattr(
        "app.db.middleware._resolve_schema_from_request",
        lambda _request: tenant_schema,
    )

    def _tenant_context() -> TenantContext:
        session = get_db_session()
        session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": e2e_user_id},
        )
        return TenantContext(
            user_id=e2e_user_id,
            practice_id="test-tenant",
            practice_schema=tenant_schema,
        )

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: e2e_user_id
    fastapi_app.dependency_overrides[get_current_user] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_active_subscription] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_tenant_context] = _tenant_context

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


def _truncate_tenant_tables(engine: Engine, schema: str) -> None:
    """Reset tenant tables between tests to keep assertions independent."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("TRUNCATE TABLE patient_clinicians, patients, audit_logs RESTART IDENTITY CASCADE")
        )
        conn.commit()


class TestPostPatientsEndToEnd:
    """``POST /api/patients`` must succeed in a freshly-provisioned tenant.

    Reproduces the pentest path: first patient ever inserted into a
    just-created tenant schema with RLS enabled. The pre-fix
    expectation is a 500 with a FK violation on
    ``patient_clinicians_patient_id_fkey``; once that's resolved this
    test guards against the regression.
    """

    def test_first_patient_creation_returns_201(
        self,
        e2e_client: TestClient,
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        _truncate_tenant_tables(engine, tenant_schema)

        response = e2e_client.post(
            "/api/patients",
            json={"first_name": "Ada", "last_name": "Lovelace"},
        )

        # The pentest finding bubbled up as 500 — assert the success
        # path explicitly so the failure mode (status, body) is
        # captured in the assertion output.
        assert response.status_code == 201, (
            f"Expected 201, got {response.status_code}. Body: {response.text}"
        )
        body = response.json()
        assert body["first_name"] == "Ada"
        assert body["last_name"] == "Lovelace"
        patient_id = body["id"]

        # Verify the rows landed and are visible under tenant RLS.
        # Use a single connection inside a single transaction so the
        # session-level GUC ``app.current_user_id`` persists for every
        # SELECT (SQLAlchemy's per-execute autocommit otherwise resets
        # session-level state between calls).
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :uid, false)"),
                {"uid": "7b0f2f61-a44d-56fc-a4e2-5aa536f6dfc9"},
            )
            patient_row = (
                conn.execute(
                    text(
                        "SELECT id, first_name, last_name FROM patients "
                        "WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": patient_id},
                )
                .mappings()
                .one_or_none()
            )
            grant_row = (
                conn.execute(
                    text(
                        "SELECT patient_id, user_id, role FROM patient_clinicians "
                        "WHERE patient_id = CAST(:id AS uuid)"
                    ),
                    {"id": patient_id},
                )
                .mappings()
                .one_or_none()
            )

        assert patient_row is not None, "Patient row not visible to tenant session"
        assert patient_row["first_name"] == "Ada"
        assert grant_row is not None, "patient_clinicians grant not written"
        assert str(grant_row["user_id"]) == "7b0f2f61-a44d-56fc-a4e2-5aa536f6dfc9"
        assert grant_row["role"] == "primary"

    def test_second_patient_creation_also_succeeds(
        self,
        e2e_client: TestClient,
        engine: Engine,
        tenant_schema: str,
    ) -> None:
        """Sanity check that the issue (if any) isn't merely "first ever insert".

        If only the very first INSERT into a fresh schema fails, the
        prior test caught it. This test confirms a steady-state insert
        also works, which rules out a "schema-warmup" theory.
        """
        _truncate_tenant_tables(engine, tenant_schema)

        first = e2e_client.post(
            "/api/patients", json={"first_name": "Grace", "last_name": "Hopper"}
        )
        assert first.status_code == 201, first.text

        second = e2e_client.post(
            "/api/patients", json={"first_name": "Alan", "last_name": "Turing"}
        )
        assert second.status_code == 201, second.text

        listed = e2e_client.get("/api/patients")
        assert listed.status_code == 200, listed.text
        names = {(p["first_name"], p["last_name"]) for p in listed.json()["data"]}
        assert ("Grace", "Hopper") in names
        assert ("Alan", "Turing") in names
