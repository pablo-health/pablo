# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""End-to-end platform audit writes against real PostgreSQL.

Mirrors ``test_audit_writes.py`` for the *platform* audit pipeline —
``PlatformAuditService`` → ``PostgresPlatformAuditRepository`` →
``platform.platform_audit_logs``. The unit suite at
``tests/test_platform_audit.py`` covers the in-memory repository only;
this suite proves the schema, ORM model, and JSONB ``details`` column
round-trip against a real database.

The 2026-05-03 pentest's PABLO-002 finding (``audit_logs`` empty over
24h) is partly a probe gap — it never queries
``platform.platform_audit_logs``. This test guards the *write* side so
any regression that breaks the platform-audit path fails CI before it
reaches main.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA
from app.db.models import Base
from app.db.platform_models import PlatformBase
from app.models.audit import AUDIT_LOG_RETENTION_DAYS
from app.models.platform_audit import (
    PlatformAuditAction,
    PlatformResourceType,
)
from app.repositories.postgres.platform_audit import PostgresPlatformAuditRepository
from app.services.platform_audit_service import PlatformAuditService
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    """Materialize tables from ORM models directly. See
    ``test_audit_writes.py::engine`` for the rationale on bypassing
    alembic here."""
    db_url = os.environ["DATABASE_URL"]
    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DEFAULT_PRACTICE_SCHEMA}"))
        conn.execute(
            text(f"SET search_path = {DEFAULT_PRACTICE_SCHEMA}, {PLATFORM_SCHEMA}, public")
        )
        PlatformBase.metadata.create_all(conn)
        Base.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.execute(text("SET search_path = practice, platform, public"))
    session.execute(text("TRUNCATE TABLE platform.platform_audit_logs"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _build_request(
    client_ip: str = "198.51.100.7",
    user_agent: str = "pytest-integration/1.0",
) -> MagicMock:
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_ip
    request.headers = {"User-Agent": user_agent}
    return request


def _count_rows(pg_session: Session) -> int:
    return pg_session.execute(
        text("SELECT COUNT(*) FROM platform.platform_audit_logs")
    ).scalar()


def _fetch_only_row(pg_session: Session) -> dict:
    row = (
        pg_session.execute(
            text(
                "SELECT id, actor_user_id, action, resource_type, resource_id, "
                "tenant_schema, ip_address, user_agent, details, "
                "timestamp, expires_at "
                "FROM platform.platform_audit_logs"
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


class TestLogTenantAction:
    @pytest.mark.parametrize("action", list(PlatformAuditAction))
    def test_writes_row_for_each_action(
        self, pg_session: Session, action: PlatformAuditAction
    ) -> None:
        service = PlatformAuditService(PostgresPlatformAuditRepository(pg_session))
        entry = service.log_tenant_action(
            action=action,
            actor_user_id="admin-user-1",
            tenant_schema="practice_acme",
            tenant_id="tenant-acme",
            request=_build_request(),
            details={"reason": "integration-test"},
        )
        pg_session.commit()

        assert _count_rows(pg_session) == 1
        row = _fetch_only_row(pg_session)
        assert row["id"] == entry.id
        assert row["actor_user_id"] == "admin-user-1"
        assert row["action"] == action.value
        assert row["resource_type"] == PlatformResourceType.TENANT.value
        assert row["resource_id"] == "tenant-acme"
        assert row["tenant_schema"] == "practice_acme"
        assert row["ip_address"] == "198.51.100.7"
        assert row["user_agent"] == "pytest-integration/1.0"
        assert row["details"] == {"reason": "integration-test"}
        # ``timestamp`` and ``expires_at`` come from independent
        # ``datetime.now()`` calls in the dataclass factory, so they
        # differ by microseconds. Verify retention to day granularity.
        delta_days = (row["expires_at"] - row["timestamp"]).days
        assert AUDIT_LOG_RETENTION_DAYS - 1 <= delta_days <= AUDIT_LOG_RETENTION_DAYS


class TestRequestContextHandling:
    def test_no_request_yields_null_ip_and_user_agent(
        self, pg_session: Session
    ) -> None:
        service = PlatformAuditService(PostgresPlatformAuditRepository(pg_session))
        service.log_tenant_action(
            action=PlatformAuditAction.TENANT_PROVISIONED,
            actor_user_id="admin-user-2",
            tenant_schema="practice_beta",
            tenant_id="tenant-beta",
            request=None,
        )
        pg_session.commit()

        row = _fetch_only_row(pg_session)
        assert row["ip_address"] is None
        assert row["user_agent"] is None
        assert row["details"] is None


class TestProbeBlindSpot:
    """Documents that platform audits are invisible to the unqualified
    ``audit_logs`` count the pentest probe runs — see PABLO-002 in the
    2026-05-03 report. If a future change re-routes platform writes
    through ``audit_logs`` (or vice versa), this test fails and the
    probe should be reviewed at the same time.
    """

    def test_platform_writes_do_not_appear_in_unqualified_audit_logs(
        self, pg_session: Session
    ) -> None:
        # Compare unqualified count before and after — robust to
        # whatever rows tenant-suite tests left in practice.audit_logs.
        unqualified_before = pg_session.execute(
            text("SELECT COUNT(*) FROM audit_logs")
        ).scalar()
        platform_before = pg_session.execute(
            text("SELECT COUNT(*) FROM platform.platform_audit_logs")
        ).scalar()

        service = PlatformAuditService(PostgresPlatformAuditRepository(pg_session))
        service.log_tenant_action(
            action=PlatformAuditAction.TENANT_PROVISIONED,
            actor_user_id="admin-probe-test",
            tenant_schema="practice_probe",
            tenant_id="tenant-probe",
            request=_build_request(),
        )
        pg_session.commit()

        # The platform row landed in platform.platform_audit_logs.
        assert (
            pg_session.execute(
                text("SELECT COUNT(*) FROM platform.platform_audit_logs")
            ).scalar()
            == platform_before + 1
        )

        # …and is invisible to the unqualified `audit_logs` lookup the
        # pentest probe runs (which resolves to practice.audit_logs via
        # search_path).
        assert (
            pg_session.execute(text("SELECT COUNT(*) FROM audit_logs")).scalar()
            == unqualified_before
        )
