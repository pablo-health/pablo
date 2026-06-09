# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end audit smoke: ``GET /api/sessions`` against a real Postgres
audit path.

The list endpoint no longer emits a count-only ``session_listed`` event.
Because it embeds the full SOAP note in each item, it now writes a
per-record ``session_viewed`` event for each session it returns — the
same audit-of-record as the detail view. These tests pin that behavior
against a real Postgres-backed ``AuditService`` (empty list → no rows;
populated list → one persisted ``session_viewed`` per session), guarding
the alembic/ORM/repo wiring that a mocked-service unit test cannot.

Requires:
  - ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``
  - ``audit_logs`` table present (``make db-up && make db-migrate``)

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

# ``ENVIRONMENT=development`` MUST be set before any ``app.*`` import.
# ``app.settings.get_settings`` is ``lru_cache``'d on first read, and
# the HTTPSEnforcementMiddleware short-circuits HTTP only when
# ``settings.is_development``. If this lands after the imports below,
# Settings caches with ``ENVIRONMENT`` unset (production-like) and the
# TestClient's ``http://testserver/`` requests all get 400.
os.environ.setdefault("ENVIRONMENT", "development")

import pytest
from app.auth.service import (
    TenantContext,
    get_current_user,
    get_current_user_id,
    get_current_user_no_mfa,
    get_tenant_context,
    require_baa_acceptance,
)
from app.models import Patient, SessionStatus, TherapySession, Transcript, User
from app.repositories import (
    InMemoryNotesRepository,
    InMemoryPatientRepository,
    InMemoryTherapySessionRepository,
)
from app.repositories.postgres.audit import PostgresAuditRepository
from app.routes.sessions import (
    get_notes_repository as get_sessions_notes_repository,
)
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
        id="90f4936f-8aa2-5988-b6ca-906781bc08c7",
        email="e2e@example.com",
        name="E2E Test User",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def session_repo() -> InMemoryTherapySessionRepository:
    return InMemoryTherapySessionRepository()


@pytest.fixture
def patient_repo(
    session_repo: InMemoryTherapySessionRepository,
) -> InMemoryPatientRepository:
    return InMemoryPatientRepository(session_repo=session_repo)


@pytest.fixture
def e2e_client(
    fastapi_app: FastAPI,
    pg_session: Session,
    e2e_user: User,
    session_repo: InMemoryTherapySessionRepository,
    patient_repo: InMemoryPatientRepository,
) -> Iterator[TestClient]:
    """TestClient with auth + repos mocked, audit service real (Postgres-backed).

    Binds ``get_audit_service`` to ``pg_session`` — the same session
    the test queries for assertions — so the test reads the pending
    row via ``expire_all()`` without fighting transaction visibility.
    """

    def _audit_service() -> AuditService:
        return AuditService(PostgresAuditRepository(pg_session))

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: e2e_user.id
    fastapi_app.dependency_overrides[get_current_user] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_session_repository] = lambda: session_repo
    fastapi_app.dependency_overrides[get_sessions_patient_repository] = lambda: patient_repo
    # ``routes/sessions.py::get_notes_repository`` carries a tenant-context
    # sub-dep that runs against the real auth chain (which we don't wire
    # up here); override it so the in-memory repo is used directly.
    notes_repo = InMemoryNotesRepository()
    fastapi_app.dependency_overrides[get_sessions_notes_repository] = lambda: notes_repo
    # ``routes/sessions.py``'s patient/notes/session repo factories all
    # depend on ``get_tenant_context``. Even when we override the repo
    # factories themselves, the route signature now (post-pablo#252)
    # threads ``get_tenant_context`` through other paths — provide a
    # no-op TenantContext so the dep resolves without a Firebase token.
    fastapi_app.dependency_overrides[get_tenant_context] = lambda: TenantContext(
        user_id=e2e_user.id,
        practice_id="test-tenant",
        practice_schema="practice",
    )
    fastapi_app.dependency_overrides[get_audit_service] = _audit_service

    try:
        yield TestClient(fastapi_app)
    finally:
        fastapi_app.dependency_overrides.clear()


class TestSessionsListAuditBehavior:
    def test_empty_list_resolves_against_real_postgres_audit_path(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        """An empty list returns 200 and writes no rows — there are no
        records to view.

        Guards the list path from 500'ing against a real Postgres-backed
        ``AuditService`` (the 2026-04-17 dev outage class).
        """
        response = e2e_client.get("/api/sessions")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["data"] == []
        assert body["total"] == 0

        pg_session.expire_all()
        rows = (
            pg_session.execute(text("SELECT action FROM practice.audit_logs"))
            .mappings()
            .all()
        )
        assert rows == []

    def test_list_writes_session_viewed_row_per_returned_session(
        self,
        e2e_client: TestClient,
        pg_session: Session,
        session_repo: InMemoryTherapySessionRepository,
        patient_repo: InMemoryPatientRepository,
        e2e_user: User,
    ) -> None:
        """The list embeds full SOAP content per item, so each returned
        session writes a ``session_viewed`` row that actually persists to
        ``practice.audit_logs`` — exercising the alembic/ORM/repo write path
        a mocked-service unit test cannot.
        """
        patient = patient_repo.create(
            Patient(
                id=str(uuid.uuid4()),
                first_name="List",
                last_name="Audit",
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                updated_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
            e2e_user.id,
        )
        session = session_repo.create(
            TherapySession(
                id=str(uuid.uuid4()),
                user_id=e2e_user.id,
                patient_id=patient.id,
                session_date=datetime(2024, 6, 15, tzinfo=UTC),
                session_number=1,
                status=SessionStatus.PENDING_REVIEW,
                transcript=Transcript(format="txt", content="t"),
                created_at=datetime(2024, 6, 15, tzinfo=UTC),
            )
        )

        response = e2e_client.get("/api/sessions")
        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1

        pg_session.expire_all()
        rows = (
            pg_session.execute(
                text("SELECT action, resource_type, resource_id FROM practice.audit_logs")
            )
            .mappings()
            .all()
        )
        assert len(rows) == 1
        row = dict(rows[0])
        assert row["action"] == "session_viewed"
        assert row["resource_type"] == "session"
        assert row["resource_id"] == session.id
