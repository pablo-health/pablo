# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Shared harness for audit-review flag tests against real PostgreSQL.

The unit suite covers the audit-review flags with in-memory repositories;
these fixtures let the ``test_audit_review_*_db.py`` modules prove the
same definitions against the *Postgres* implementations — the novelty
SQL (``GROUP BY … HAVING min(timestamp)`` warmup, baseline pair dedup)
and the grant-gated appointment/session lookups have no in-memory
equivalent.

One flag = one test module. Adding a new flag (e.g. source novelty)
means adding one module on top of this harness, not a new harness.

NOTE: no ``app.*`` imports at module level. pytest imports nested
conftests as plugins *before* ``pytest_configure`` in the parent
conftest exports ``DATABASE_URL``, and app settings are cached at first
import — a top-level app import here would freeze settings without a
database URL and break every module in this directory. All app imports
live inside the fixtures, which run post-configure.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories.postgres.appointment import PostgresAppointmentRepository
    from app.repositories.postgres.audit import PostgresAuditRepository
    from app.repositories.postgres.patient import PostgresPatientRepository
    from app.repositories.postgres.session import PostgresTherapySessionRepository
    from app.repositories.postgres.user import PostgresUserRepository
    from app.services.audit_review_service import AuditReviewService
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_TRUNCATE_SQL = (
    "TRUNCATE TABLE practice.audit_logs, practice.appointments, "
    "practice.therapy_sessions, practice.patient_clinicians, "
    "practice.patients, platform.users CASCADE"
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


@dataclass
class AuditReviewHarness:
    """Seeding helpers + the service under test, bound to one pg session.

    All timestamps are expressed as offsets from "now" so tests read as
    the flag definitions do ("viewed 8 days ago", "appointment in 3
    days") and never go stale.
    """

    session: Session
    audit_repo: PostgresAuditRepository
    patient_repo: PostgresPatientRepository
    user_repo: PostgresUserRepository
    appointment_repo: PostgresAppointmentRepository
    session_repo: PostgresTherapySessionRepository
    service: AuditReviewService

    def seed_user(self, name: str = "Alex Reviewer") -> str:
        """Create a platform user row; returns the user id."""
        from app.db.platform_models import PlatformUserRow  # noqa: PLC0415

        uid = str(uuid.uuid4())
        self.session.add(
            PlatformUserRow(
                id=uid,
                email=f"{uid[:13]}@example.com",
                name=name,
                created_at=_now() - timedelta(days=400),
            )
        )
        self.session.flush()
        return uid

    def seed_patient(
        self,
        owner_user_id: str,
        *,
        last_name: str = "Patientson",
        created_days_ago: float = 60.0,
        log_create: bool = True,
    ) -> str:
        """Create a patient owned (primary grant) by ``owner_user_id``.

        The review service derives patient age from the PATIENT_CREATED
        audit row, so one is appended at ``created_days_ago`` unless the
        test wants a patient with no recorded create (``log_create=False``).
        """
        from app.models import Patient  # noqa: PLC0415

        created = _now() - timedelta(days=created_days_ago)
        patient = Patient(
            id=str(uuid.uuid4()),
            first_name="Synthetic",
            last_name=last_name,
            created_at=created,
            updated_at=created,
        )
        self.patient_repo.create(patient, owner_user_id)
        if log_create:
            self.audit(
                owner_user_id,
                "patient_created",
                patient_id=patient.id,
                days_ago=created_days_ago,
            )
        return patient.id

    def audit(  # noqa: PLR0913 — one keyword arg per audit-row column by design
        self,
        user_id: str,
        action: str,
        *,
        patient_id: str | None = None,
        days_ago: float = 0.0,
        hours_ago: float = 0.0,
        ip_address: str = "203.0.113.10",
        user_agent: str = "pytest-integration/1.0",
        changes: dict[str, Any] | None = None,
    ) -> None:
        """Append one audit row at ``now - days_ago - hours_ago``."""
        from app.models.audit import AuditLogEntry  # noqa: PLC0415

        ts = _now() - timedelta(days=days_ago, hours=hours_ago)
        self.audit_repo.append(
            AuditLogEntry(
                user_id=user_id,
                action=action,
                resource_type="patient" if patient_id else "admin",
                resource_id=patient_id or "admin-resource",
                patient_id=patient_id,
                timestamp=_iso(ts),
                ip_address=ip_address,
                user_agent=user_agent,
                changes=changes,
            )
        )

    def seed_appointment(
        self,
        user_id: str,
        patient_id: str,
        *,
        start_days_from_now: float = 0.0,
        status: str = "scheduled",
    ) -> None:
        from app.scheduling_engine.models.appointment import Appointment  # noqa: PLC0415

        start = _now() + timedelta(days=start_days_from_now)
        self.appointment_repo.create(
            Appointment(
                id=str(uuid.uuid4()),
                user_id=user_id,
                patient_id=patient_id,
                title="session",
                start_at=start,
                end_at=start + timedelta(hours=1),
                duration_minutes=60,
                status=status,
                session_type="individual",
                # Migration-built appointments tables (vs the ORM-only
                # create_all path) have NOT NULL audit columns with no
                # server default — stamp them so the harness works on both.
                created_at=_now(),
                updated_at=_now(),
            )
        )

    def seed_therapy_session(
        self,
        user_id: str,
        patient_id: str,
        *,
        days_ago: float = 0.0,
        session_number: int = 1,
        status: str = "finalized",
    ) -> None:
        from app.models.session import TherapySession, Transcript  # noqa: PLC0415

        session_date = _now() - timedelta(days=days_ago)
        self.session_repo.create(
            TherapySession(
                id=str(uuid.uuid4()),
                user_id=user_id,
                patient_id=patient_id,
                session_date=session_date,
                session_number=session_number,
                status=status,
                transcript=Transcript(format="text", content="dummy"),
                created_at=session_date,
            )
        )

    def payload(self, window_hours: int = 24) -> dict:
        """Flush pending seeds and run the service under test."""
        self.session.flush()
        return self.service.compute_payload(window_hours=window_hours).to_dict()


@pytest.fixture(scope="session")
def audit_review_engine() -> Iterator[Engine]:
    """ORM-materialized schema, same rationale as ``test_audit_writes``:
    migration correctness is proven elsewhere; the service sees the
    schema the running app uses."""
    from app.db import DEFAULT_PRACTICE_SCHEMA, PLATFORM_SCHEMA  # noqa: PLC0415
    from app.db.models import Base  # noqa: PLC0415
    from app.db.platform_models import PlatformBase  # noqa: PLC0415
    from sqlalchemy import create_engine, text  # noqa: PLC0415

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
def audit_review(audit_review_engine: Engine) -> Iterator[AuditReviewHarness]:
    """Function-scoped harness over a clean slate of all involved tables."""
    from app.db import _current_tenant_schema, set_tenant_schema  # noqa: PLC0415
    from app.repositories.postgres.appointment import (  # noqa: PLC0415
        PostgresAppointmentRepository,
    )
    from app.repositories.postgres.audit import PostgresAuditRepository  # noqa: PLC0415
    from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415
    from app.repositories.postgres.session import (  # noqa: PLC0415
        PostgresTherapySessionRepository,
    )
    from app.repositories.postgres.user import PostgresUserRepository  # noqa: PLC0415
    from app.services.audit_review_service import AuditReviewService  # noqa: PLC0415
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.orm import sessionmaker  # noqa: PLC0415

    factory = sessionmaker(bind=audit_review_engine, expire_on_commit=False)
    session = factory()
    set_tenant_schema(session)
    session.execute(text(_TRUNCATE_SQL))
    session.commit()
    set_tenant_schema(session)
    try:
        audit_repo = PostgresAuditRepository(session)
        patient_repo = PostgresPatientRepository(session)
        user_repo = PostgresUserRepository(session)
        appointment_repo = PostgresAppointmentRepository(session)
        session_repo = PostgresTherapySessionRepository(session)
        yield AuditReviewHarness(
            session=session,
            audit_repo=audit_repo,
            patient_repo=patient_repo,
            user_repo=user_repo,
            appointment_repo=appointment_repo,
            session_repo=session_repo,
            service=AuditReviewService(
                audit_repo=audit_repo,
                patient_repo=patient_repo,
                user_repo=user_repo,
                appointment_repo=appointment_repo,
                session_repo=session_repo,
            ),
        )
    finally:
        _current_tenant_schema.set(None)
        session.rollback()
        session.close()
