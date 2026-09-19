# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``GET /api/users/me/audit-log`` paging, against a real Postgres.

The unit suite pages the in-memory repository, which compares Python
tuples. The shipped query does not: it asks Postgres to compare a row
value, ``(timestamp, id) < (:ts, :id)``, with the id column typed as a
uuid and the bind a string. Whether that predicate is even accepted —
let alone whether it orders the way the in-memory list does — is a fact
about Postgres and SQLAlchemy, not about our Python.

So this walks the user's whole history through the API the way the page
does, and asserts the walk is complete: every row seen exactly once, the
rows that share a timestamp included, and an end that announces itself.

Requires:
  - ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``
  - ``audit_logs`` table present (``make db-up && make db-migrate``)

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

# ``ENVIRONMENT=development`` MUST be set before any ``app.*`` import —
# see the note in test_sessions_audit_e2e.py.
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
from app.db import set_tenant_schema
from app.models import User
from app.models.audit import AuditLogEntry
from app.repositories.postgres.audit import PostgresAuditRepository
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

USER_ID = "90f4936f-8aa2-5988-b6ca-906781bc08c7"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    # Go through the app's own tenant-schema plumbing rather than a bare
    # SET. This test seeds, commits, and then reads back through the API,
    # and the pool's checkin listener deliberately scrubs search_path on
    # the way back — only ``set_tenant_schema`` leaves the ContextVar the
    # checkout listener re-applies from.
    set_tenant_schema(session, "practice")
    # audit_logs is append-only; arm the purge GUC so the BEFORE TRUNCATE
    # trigger allows this authorized fixture reset (same transaction).
    session.execute(text("SET LOCAL app.allow_audit_purge = 'on'"))
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
        id=USER_ID,
        email="e2e@example.com",
        name="E2E Test User",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


@pytest.fixture
def e2e_client(
    fastapi_app: FastAPI,
    pg_session: Session,
    e2e_user: User,
) -> Iterator[TestClient]:
    def _audit_service() -> AuditService:
        return AuditService(PostgresAuditRepository(pg_session))

    fastapi_app.dependency_overrides[get_current_user_id] = lambda: e2e_user.id
    fastapi_app.dependency_overrides[get_current_user] = lambda: e2e_user
    fastapi_app.dependency_overrides[get_current_user_no_mfa] = lambda: e2e_user
    fastapi_app.dependency_overrides[require_baa_acceptance] = lambda: e2e_user
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


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def _seed(session: Session, *, distinct_rows: int, tied_rows: int) -> int:
    """Write a history worth paging: mostly distinct times, one tied batch.

    The tie is the interesting part — rows written inside one transaction
    really do land on the same microsecond, and that is exactly where a
    timestamp-only cursor loses rows.
    """
    repo = PostgresAuditRepository(session)
    base = datetime.now(UTC)
    for i in range(distinct_rows):
        repo.append(
            AuditLogEntry(
                user_id=USER_ID,
                action="patient_viewed",
                resource_type="patient",
                resource_id=f"resource-{i}",
                timestamp=_iso(base - timedelta(seconds=i + 1)),
            )
        )
    shared = _iso(base - timedelta(seconds=distinct_rows + 1))
    for i in range(tied_rows):
        repo.append(
            AuditLogEntry(
                user_id=USER_ID,
                action="patient_viewed",
                resource_type="patient",
                resource_id=f"tied-{i}",
                timestamp=shared,
            )
        )
    # A second user's rows, to prove paging never wanders out of the caller's
    # own trail partway down the history.
    for i in range(5):
        repo.append(
            AuditLogEntry(
                user_id="someone-else",
                action="patient_viewed",
                resource_type="patient",
                resource_id=f"other-{i}",
                timestamp=_iso(base - timedelta(seconds=i + 1)),
            )
        )
    session.commit()
    return distinct_rows + tied_rows


def _walk(client: TestClient, *, limit: int) -> list[dict]:
    """Page to the end the way the UI's 'load more' does."""
    rows: list[dict] = []
    cursor: str | None = None
    for _ in range(50):  # loop bound: a runaway cursor fails, never hangs
        query = f"/api/users/me/audit-log?limit={limit}"
        if cursor:
            query += f"&cursor={cursor}"
        body = client.get(query).json()
        rows.extend(body["data"])
        cursor = body["next_cursor"]
        if cursor is None:
            return rows
    raise AssertionError("paging did not terminate")


class TestSelfAuditLogPagingAgainstPostgres:
    def test_walks_the_whole_history_exactly_once(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        seeded = _seed(pg_session, distinct_rows=17, tied_rows=6)

        rows = _walk(e2e_client, limit=5)

        ids = [r["id"] for r in rows]
        assert len(ids) == len(set(ids)), "a row was served on two pages"
        # Every seeded row is present. The walk also picks up the
        # self-audit-view rows each request writes, so the count is a floor,
        # not an equality — what matters is that nothing seeded went missing.
        seeded_ids = {
            # str(): the column is a uuid, the API renders it as text.
            str(row_id)
            for (row_id,) in pg_session.execute(
                text(
                    "SELECT id FROM practice.audit_logs WHERE user_id = :uid "
                    "AND (resource_id LIKE 'resource-%' OR resource_id LIKE 'tied-%')"
                ),
                {"uid": USER_ID},
            ).all()
        }
        assert len(seeded_ids) == seeded
        assert seeded_ids <= set(ids)

    def test_rows_sharing_a_timestamp_survive_a_page_boundary(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        # Six rows on one timestamp, paged three at a time: the boundary
        # falls inside the tied group.
        _seed(pg_session, distinct_rows=3, tied_rows=6)

        rows = _walk(e2e_client, limit=3)

        tied = [r for r in rows if r["resource_id"].startswith("tied-")]
        assert len(tied) == 6
        assert len({r["id"] for r in tied}) == 6

    def test_stays_inside_the_callers_own_trail(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        _seed(pg_session, distinct_rows=9, tied_rows=3)

        rows = _walk(e2e_client, limit=4)

        assert not [r for r in rows if r["resource_id"].startswith("other-")]

    def test_newest_first_all_the_way_down(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        _seed(pg_session, distinct_rows=12, tied_rows=4)

        timestamps = [r["timestamp"] for r in _walk(e2e_client, limit=5)]

        assert timestamps == sorted(timestamps, reverse=True)

    def test_last_page_says_it_is_the_last(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        _seed(pg_session, distinct_rows=3, tied_rows=0)

        body = e2e_client.get("/api/users/me/audit-log?limit=100").json()

        assert body["next_cursor"] is None

    def test_malformed_cursor_is_rejected_not_ignored(
        self, e2e_client: TestClient, pg_session: Session
    ) -> None:
        # Ignoring it would silently serve page one forever, which reads as
        # "this is your whole history" — the exact lie this page must not tell.
        _seed(pg_session, distinct_rows=3, tied_rows=0)

        resp = e2e_client.get("/api/users/me/audit-log?cursor=%%%not-a-cursor%%%")

        assert resp.status_code == 400
