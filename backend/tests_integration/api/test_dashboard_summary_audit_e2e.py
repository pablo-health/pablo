# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end audit smoke: ``GET /api/dashboard/summary`` against a real
Postgres audit path.

The dashboard summary replaced a panel-by-panel fan-out that fetched the
full session list (one ``session_viewed`` per row, up to a page) *and* a
blind patient page (one ``patient_viewed`` per patient) just to decorate
appointments. The summary instead audits only the awaiting-review rows it
actually returns and never reads the patient list — so the audit trail
records real disclosures, not dashboard-render noise.

These tests pin that against a real Postgres-backed ``AuditService``:
  - exactly one ``session_viewed`` per inline awaiting-review row (capped),
  - and zero ``patient_viewed`` rows.

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

# ``ENVIRONMENT=development`` MUST be set before any ``app.*`` import (see the
# sibling sessions audit e2e for the Settings-cache rationale).
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
from app.routes.scheduling import get_scheduling_service
from app.routes.sessions import (
    get_notes_repository as get_sessions_notes_repository,
)
from app.routes.sessions import (
    get_patient_repository as get_sessions_patient_repository,
)
from app.routes.sessions import get_session_repository
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService
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

# The summary caps inline awaiting-review rows; seed past it to prove the cap.
_AWAITING_REVIEW_LIMIT = 5
_TODAY = "2026-06-15T00:00:00+00:00"
_TODAY_END = "2026-06-16T00:00:00+00:00"
_WEEK = "2026-06-16T00:00:00+00:00"
_WEEK_END = "2026-06-22T00:00:00+00:00"


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
    def _audit_service() -> AuditService:
        return AuditService(PostgresAuditRepository(pg_session))

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: e2e_user.id
    fastapi_app.dependency_overrides[get_current_user] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_session_repository] = lambda: session_repo
    fastapi_app.dependency_overrides[get_sessions_patient_repository] = lambda: patient_repo
    notes_repo = InMemoryNotesRepository()
    notes_repo.grant_all_access()
    fastapi_app.dependency_overrides[get_sessions_notes_repository] = lambda: notes_repo
    fastapi_app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(
        InMemoryAppointmentRepository()
    )
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


def _summary(client: TestClient) -> dict:
    return client.get(
        "/api/dashboard/summary",
        params={
            "today_start": _TODAY,
            "today_end": _TODAY_END,
            "week_start": _WEEK,
            "week_end": _WEEK_END,
        },
    )


class TestDashboardSummaryAuditBehavior:
    def test_empty_dashboard_writes_no_audit_rows(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        """A clinician with nothing pending discloses nothing — and writes no
        audit rows — while still resolving 200 against real Postgres."""
        response = _summary(e2e_client)
        assert response.status_code == 200, response.text
        assert response.json()["awaiting_review_total"] == 0

        pg_session.expire_all()
        rows = pg_session.execute(text("SELECT action FROM practice.audit_logs")).all()
        assert rows == []

    def test_audits_only_inline_rows_and_never_patient_views(
        self,
        e2e_client: TestClient,
        pg_session: Session,
        session_repo: InMemoryTherapySessionRepository,
        patient_repo: InMemoryPatientRepository,
        e2e_user: User,
    ) -> None:
        """Seed more awaiting-review sessions than fit inline. The summary must
        persist exactly one ``session_viewed`` per inline row (capped) and zero
        ``patient_viewed`` rows — the blind patient-list read is gone."""
        patient = patient_repo.create(
            Patient(
                id=str(uuid.uuid4()),
                first_name="Real",
                last_name="DB",
                created_at=datetime(2024, 1, 1, tzinfo=UTC),
                updated_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
            e2e_user.id,
        )
        seeded = _AWAITING_REVIEW_LIMIT + 1
        for i in range(seeded):
            session_repo.create(
                TherapySession(
                    id=str(uuid.uuid4()),
                    user_id=e2e_user.id,
                    patient_id=patient.id,
                    session_date=datetime(2024, 6, 15, 10, i, tzinfo=UTC),
                    session_number=i + 1,
                    status=SessionStatus.PENDING_REVIEW,
                    transcript=Transcript(format="txt", content="t"),
                    created_at=datetime(2024, 6, 15, tzinfo=UTC),
                )
            )

        response = _summary(e2e_client)
        assert response.status_code == 200, response.text
        body = response.json()
        # Full count over the seeded set; inline rows capped.
        assert body["awaiting_review_total"] == seeded
        assert len(body["awaiting_review"]) == _AWAITING_REVIEW_LIMIT

        pg_session.expire_all()
        rows = (
            pg_session.execute(text("SELECT action FROM practice.audit_logs"))
            .mappings()
            .all()
        )
        actions = [r["action"] for r in rows]
        # One session_viewed per inline row — and nothing else.
        assert actions == ["session_viewed"] * _AWAITING_REVIEW_LIMIT
        assert "patient_viewed" not in actions
