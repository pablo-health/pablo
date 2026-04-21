# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end audit writes against real PostgreSQL.

Exercises every ``AuditService.log_*`` method against a real
``PostgresAuditRepository`` backed by a migrated database. The unit
suite patches out ``AuditService`` entirely and therefore cannot catch
schema/repo drift — which is how the dev 500 on 2026-04-17 slipped
through (``practice.audit_logs`` missing, nothing to patch against).

What a unit test cannot prove but these tests do:
  - the ORM model matches the migration (no missing columns)
  - JSONB round-trips the ``changes`` field without mangling types
  - each ``log_*`` call writes exactly one row with the right shape
  - every ``AuditAction`` value fits within the 50-char column bound

Requires:
  - ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``
  - ``audit_logs`` table present (``make db-up && make db-migrate``)

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from alembic import command
from alembic.config import Config
from app.models import Patient, User
from app.models.audit import AuditAction, ResourceType
from app.models.session import TherapySession, Transcript
from app.repositories.postgres.audit import PostgresAuditRepository
from app.services.audit_service import AuditService
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")

    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    """Function-scoped session with ``search_path = practice, platform, public``.

    Truncates ``practice.audit_logs`` before the test so row counts are
    deterministic. Assumes the table exists — run ``make db-migrate``
    once before invoking the integration suite.
    """
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


# ─── Helpers ─────────────────────────────────────────────────────────────


def _build_service(pg_session: Session) -> AuditService:
    return AuditService(PostgresAuditRepository(pg_session))


def _build_user(user_id: str = "test-user-1") -> User:
    return User(
        id=user_id,
        email="integration@example.com",
        name="Integration Test User",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_accepted_at=datetime(2024, 1, 1, tzinfo=UTC),
        baa_version="2024-01-01",
    )


def _build_patient(patient_id: str = "patient-1") -> Patient:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return Patient(
        id=patient_id,
        user_id="test-user-1",
        first_name="Jane",
        last_name="Doe",
        created_at=now,
        updated_at=now,
    )


def _build_session(
    session_id: str = "session-1", patient_id: str = "patient-1"
) -> TherapySession:
    return TherapySession(
        id=session_id,
        user_id="test-user-1",
        patient_id=patient_id,
        session_date=datetime(2024, 6, 1, tzinfo=UTC),
        session_number=1,
        status="pending_review",
        transcript=Transcript(format="text", content="dummy"),
        created_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def _build_request(
    client_ip: str = "198.51.100.7",
    forwarded_for: str | None = None,
    user_agent: str = "pytest-integration/1.0",
) -> MagicMock:
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_ip
    request.headers = {"User-Agent": user_agent}
    if forwarded_for is not None:
        request.headers["X-Forwarded-For"] = forwarded_for
    return request


def _count_rows(pg_session: Session) -> int:
    return pg_session.execute(text("SELECT COUNT(*) FROM practice.audit_logs")).scalar()


def _fetch_only_row(pg_session: Session) -> dict:
    row = (
        pg_session.execute(
            text(
                "SELECT id, user_id, action, resource_type, resource_id, "
                "patient_id, session_id, ip_address, user_agent, changes, "
                "timestamp, expires_at "
                "FROM practice.audit_logs"
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


# ─── log() generic path ──────────────────────────────────────────────────


class TestGenericLog:
    def test_writes_single_row_with_expected_shape(self, pg_session: Session) -> None:
        service = _build_service(pg_session)
        user = _build_user()
        patient = _build_patient()

        entry = service.log(
            action=AuditAction.PATIENT_VIEWED,
            user=user,
            request=_build_request(),
            resource_type=ResourceType.PATIENT,
            resource_id=patient.id,
            patient=patient,
        )
        pg_session.commit()

        assert _count_rows(pg_session) == 1
        row = _fetch_only_row(pg_session)
        assert row["id"] == entry.id
        assert row["user_id"] == user.id
        assert row["action"] == "patient_viewed"
        assert row["resource_type"] == "patient"
        assert row["resource_id"] == patient.id
        assert row["patient_id"] == patient.id
        assert row["session_id"] is None
        assert row["ip_address"] == "198.51.100.7"
        assert row["user_agent"] == "pytest-integration/1.0"
        assert row["changes"] is None
        # Retention stamp should land ~7y in the future (2555 days)
        delta_days = (row["expires_at"] - row["timestamp"]).days
        assert 2554 <= delta_days <= 2556

    def test_x_forwarded_for_takes_precedence_over_client_host(
        self, pg_session: Session
    ) -> None:
        service = _build_service(pg_session)
        service.log(
            action=AuditAction.PATIENT_VIEWED,
            user=_build_user(),
            request=_build_request(forwarded_for="203.0.113.9, 10.0.0.1"),
            resource_type=ResourceType.PATIENT,
            resource_id="p1",
        )
        pg_session.commit()
        row = _fetch_only_row(pg_session)
        assert row["ip_address"] == "203.0.113.9"

    def test_changes_with_phi_field_name_is_rejected_before_write(
        self, pg_session: Session
    ) -> None:
        service = _build_service(pg_session)
        with pytest.raises(ValueError, match="PHI field name"):
            service.log(
                action=AuditAction.PATIENT_UPDATED,
                user=_build_user(),
                request=_build_request(),
                resource_type=ResourceType.PATIENT,
                resource_id="p1",
                changes={"first_name": {"old": "Jane", "new": "Janet"}},
            )
        pg_session.commit()
        assert _count_rows(pg_session) == 0


# ─── log_patient_action() ────────────────────────────────────────────────


class TestLogPatientAction:
    @pytest.mark.parametrize(
        "action",
        [
            AuditAction.PATIENT_CREATED,
            AuditAction.PATIENT_VIEWED,
            AuditAction.PATIENT_UPDATED,
            AuditAction.PATIENT_DELETED,
            AuditAction.PATIENT_EXPORTED,
        ],
    )
    def test_writes_row_for_each_patient_action(
        self, pg_session: Session, action: AuditAction
    ) -> None:
        service = _build_service(pg_session)
        patient = _build_patient()

        service.log_patient_action(
            action=action,
            user=_build_user(),
            request=_build_request(),
            patient=patient,
            changes={"changed_fields": ["status"]}
            if action == AuditAction.PATIENT_UPDATED
            else None,
        )
        pg_session.commit()

        row = _fetch_only_row(pg_session)
        assert row["action"] == action.value
        assert row["resource_type"] == "patient"
        assert row["resource_id"] == patient.id
        assert row["patient_id"] == patient.id
        if action == AuditAction.PATIENT_UPDATED:
            assert row["changes"] == {"changed_fields": ["status"]}


# ─── log_session_action() ────────────────────────────────────────────────


class TestLogSessionAction:
    @pytest.mark.parametrize(
        "action",
        [
            AuditAction.SESSION_CREATED,
            AuditAction.SESSION_VIEWED,
            AuditAction.SESSION_FINALIZED,
            AuditAction.SESSION_RATING_UPDATED,
        ],
    )
    def test_writes_row_for_each_session_action(
        self, pg_session: Session, action: AuditAction
    ) -> None:
        service = _build_service(pg_session)
        patient = _build_patient()
        session_model = _build_session(patient_id=patient.id)

        service.log_session_action(
            action=action,
            user=_build_user(),
            request=_build_request(),
            session=session_model,
            patient=patient,
            changes={"quality_rating": {"old": 3, "new": 4}}
            if action == AuditAction.SESSION_RATING_UPDATED
            else None,
        )
        pg_session.commit()

        row = _fetch_only_row(pg_session)
        assert row["action"] == action.value
        assert row["resource_type"] == "session"
        assert row["resource_id"] == session_model.id
        assert row["session_id"] == session_model.id
        assert row["patient_id"] == patient.id


# ─── list-endpoint audit helpers ─────────────────────────────────────────


class TestListLogs:
    def test_log_patient_list(self, pg_session: Session) -> None:
        service = _build_service(pg_session)
        service.log_patient_list(
            user=_build_user(), request=_build_request(), patient_count=7
        )
        pg_session.commit()
        row = _fetch_only_row(pg_session)
        assert row["action"] == "patient_listed"
        assert row["resource_type"] == "patient"
        assert row["resource_id"] == "list"
        assert row["patient_id"] is None
        assert row["changes"] == {"patient_count": 7}

    def test_log_session_list(self, pg_session: Session) -> None:
        service = _build_service(pg_session)
        service.log_session_list(
            user=_build_user(), request=_build_request(), session_count=42
        )
        pg_session.commit()
        row = _fetch_only_row(pg_session)
        assert row["action"] == "session_listed"
        assert row["resource_type"] == "session"
        assert row["resource_id"] == "list"
        assert row["session_id"] is None
        assert row["changes"] == {"session_count": 42}


# ─── log_admin_action() ──────────────────────────────────────────────────


class TestLogAdminAction:
    @pytest.mark.parametrize(
        ("action", "resource_id"),
        [
            (AuditAction.EXPORT_QUEUE_VIEWED, ""),
            (AuditAction.EXPORT_ACTION_TAKEN, "session-xyz"),
            (AuditAction.TENANT_LISTED, ""),
            (AuditAction.TENANT_VIEWED, "tenant-abc"),
            (AuditAction.TENANT_DISABLED, "tenant-abc"),
            (AuditAction.TENANT_ENABLED, "tenant-abc"),
            (AuditAction.TENANT_DELETED, "tenant-abc"),
            (AuditAction.EHR_NAVIGATE, "ehr:/session/123"),
        ],
    )
    def test_writes_row_for_each_admin_action(
        self, pg_session: Session, action: AuditAction, resource_id: str
    ) -> None:
        service = _build_service(pg_session)
        service.log_admin_action(
            action=action,
            user=_build_user(),
            request=_build_request(),
            resource_id=resource_id,
        )
        pg_session.commit()
        row = _fetch_only_row(pg_session)
        assert row["action"] == action.value
        assert row["resource_id"] == resource_id


# ─── Bulk smoke: all AuditAction values fit the schema ───────────────────


class TestSchemaFitsAllActions:
    def test_every_action_value_fits_column_bounds(self, pg_session: Session) -> None:
        """Every ``AuditAction`` value must fit the ``VARCHAR(50)`` column."""
        service = _build_service(pg_session)
        for action in AuditAction:
            service.log_admin_action(
                action=action,
                user=_build_user(),
                request=_build_request(),
                resource_id="smoke",
            )
        pg_session.commit()
        assert _count_rows(pg_session) == len(list(AuditAction))
