# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end audit smoke: ``GET /api/sessions`` writes to ``audit_logs``.

Regression test for the dev 500 we shipped on 2026-04-17 — the
``practice.audit_logs`` table was missing, so every list endpoint
raised at ``audit.log_session_list()``. A TestClient-level test with a
mocked ``AuditService`` (like the unit suite) cannot catch this — the
bug lived in the gap between alembic, the ORM model, and the repo.

Requires:
  - ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``
  - ``audit_logs`` table present (``make db-up && make db-migrate``)

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.auth.service import (
    get_current_user,
    get_current_user_id,
    get_current_user_no_mfa,
    require_baa_acceptance,
)
from app.models import User
from app.repositories import InMemoryPatientRepository, InMemoryTherapySessionRepository
from app.repositories.postgres.audit import PostgresAuditRepository
from app.routes.sessions import (
    get_patient_repository as get_sessions_patient_repository,
)
from app.routes.sessions import get_session_repository
from app.services.audit_service import AuditService, get_audit_service
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres; "
        "apply migrations with `make db-migrate`."
    ),
)

os.environ.setdefault("ENVIRONMENT", "development")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.execute(text("SET search_path = practice, platform, public"))
    session.execute(text("TRUNCATE TABLE practice.audit_logs"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(scope="module")
def fastapi_app() -> FastAPI:
    """Import the FastAPI app lazily.

    ``app.main`` runs ``ensure_schemas(get_engine())`` at import time,
    which connects to DATABASE_URL. We defer the import until after
    pytestmark has gated on ``DATABASE_URL`` being set.
    """
    from app.main import app  # noqa: PLC0415  # deferred — DB connect at import

    return app


@pytest.fixture
def e2e_user() -> User:
    return User(
        id="e2e-audit-user",
        email="e2e@example.com",
        name="E2E Test User",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def e2e_client(
    fastapi_app: FastAPI, pg_session: Session, e2e_user: User
) -> Iterator[TestClient]:
    """TestClient with auth + repos mocked, audit service real (Postgres-backed).

    Binds ``get_audit_service`` to ``pg_session`` — the same session
    the test queries for assertions — so the test reads the pending
    row via ``expire_all()`` without fighting transaction visibility.
    """
    session_repo = InMemoryTherapySessionRepository()
    patient_repo = InMemoryPatientRepository(session_repo=session_repo)

    def _audit_service() -> AuditService:
        return AuditService(PostgresAuditRepository(pg_session))

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: e2e_user.id
    fastapi_app.dependency_overrides[get_current_user] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_session_repository] = lambda: session_repo
    fastapi_app.dependency_overrides[get_sessions_patient_repository] = lambda: patient_repo
    fastapi_app.dependency_overrides[get_audit_service] = _audit_service

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


class TestSessionsListWritesAudit:
    def test_empty_list_still_writes_audit_row(
        self, e2e_client: TestClient, pg_session: Session, e2e_user: User
    ) -> None:
        """Regression for the 2026-04-17 dev outage.

        Before the fix, this call 500'd because ``audit_logs`` didn't
        exist. After the fix, it returns 200 AND writes exactly one
        ``session_listed`` row.
        """
        response = e2e_client.get("/api/sessions")
        assert response.status_code == 200
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0

        # The repo flushed but didn't commit. Expire our snapshot so
        # the SELECT sees the pending row in the same session.
        pg_session.expire_all()
        rows = (
            pg_session.execute(
                text(
                    "SELECT user_id, action, resource_type, resource_id, changes "
                    "FROM practice.audit_logs"
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["user_id"] == e2e_user.id
        assert row["action"] == "session_listed"
        assert row["resource_type"] == "session"
        assert row["resource_id"] == "list"
        assert row["changes"] == {"session_count": 0}
