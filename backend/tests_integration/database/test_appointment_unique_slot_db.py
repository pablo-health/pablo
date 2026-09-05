# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for the appointments unique active-slot index.

``SchedulingService._reject_if_overlapping`` (see
``test_appointment_overlap_db.py``) is a check-then-insert query — it
can't stop two concurrent requests from both passing the check before
either commits. ``uq_appointments_user_start_active`` is the DB-level
backstop: these tests prove the index actually rejects a genuine
duplicate insert, and does so exactly on the rows the predicate says it
should.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import (
    _current_tenant_schema,
    arm_current_user_id,
    set_tenant_schema,
)
from app.models import Patient
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.scheduling_engine.models.appointment import Appointment
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_NOW = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = f"practice_test_appt_unique_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def session(engine: Engine, tenant_schema: str) -> Iterator[Session]:
    sess = Session(bind=engine)
    set_tenant_schema(sess, tenant_schema)
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
        _current_tenant_schema.set(None)


def _arm(session: Session, user_id: str) -> None:
    session.commit()
    arm_current_user_id(session, user_id)


def _user() -> str:
    return str(uuid.uuid4())


def _seed_patient(session: Session, user_id: str) -> str:
    patient_id = str(uuid.uuid4())
    now = datetime(2026, 1, 1, tzinfo=UTC)
    PostgresPatientRepository(session).create(
        Patient(
            id=patient_id,
            first_name="Pat",
            last_name="Ient",
            created_at=now,
            updated_at=now,
        ),
        user_id,
    )
    return patient_id


def _make_appointment(
    *,
    user_id: str,
    patient_id: str,
    start: datetime,
    end: datetime,
    status: str = "confirmed",
) -> Appointment:
    return Appointment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        patient_id=patient_id,
        title="Session",
        start_at=start,
        end_at=end,
        duration_minutes=int((end - start).total_seconds() // 60),
        status=status,
        session_type="individual",
        # Migration-built appointments tables have NOT NULL audit columns
        # with no server default — stamp them explicitly.
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_second_non_cancelled_appointment_at_same_slot_raises(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    repo = PostgresAppointmentRepository(session)
    repo.create(
        _make_appointment(
            user_id=user_id, patient_id=patient_id, start=_NOW, end=_NOW + timedelta(hours=1)
        )
    )
    session.commit()

    with pytest.raises(IntegrityError):
        repo.create(
            _make_appointment(
                user_id=user_id,
                patient_id=patient_id,
                start=_NOW,
                end=_NOW + timedelta(hours=1),
            )
        )


def test_pending_hold_blocks_the_same_slot_too(session: Session) -> None:
    """A PENDING hold occupies its slot just as hard as a CONFIRMED booking."""
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    repo = PostgresAppointmentRepository(session)
    repo.create(
        _make_appointment(
            user_id=user_id,
            patient_id=patient_id,
            start=_NOW,
            end=_NOW + timedelta(hours=1),
            status="pending",
        )
    )
    session.commit()

    with pytest.raises(IntegrityError):
        repo.create(
            _make_appointment(
                user_id=user_id,
                patient_id=patient_id,
                start=_NOW,
                end=_NOW + timedelta(hours=1),
                status="confirmed",
            )
        )


def test_cancelled_appointment_does_not_block_the_slot(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    repo = PostgresAppointmentRepository(session)
    repo.create(
        _make_appointment(
            user_id=user_id,
            patient_id=patient_id,
            start=_NOW,
            end=_NOW + timedelta(hours=1),
            status="cancelled",
        )
    )
    session.commit()

    repo.create(
        _make_appointment(
            user_id=user_id, patient_id=patient_id, start=_NOW, end=_NOW + timedelta(hours=1)
        )
    )
    session.commit()


def test_different_user_at_the_same_start_does_not_collide(session: Session) -> None:
    user_a, user_b = _user(), _user()
    _arm(session, user_a)
    patient_a = _seed_patient(session, user_a)
    PostgresAppointmentRepository(session).create(
        _make_appointment(
            user_id=user_a, patient_id=patient_a, start=_NOW, end=_NOW + timedelta(hours=1)
        )
    )
    session.commit()

    _arm(session, user_b)
    patient_b = _seed_patient(session, user_b)
    PostgresAppointmentRepository(session).create(
        _make_appointment(
            user_id=user_b, patient_id=patient_b, start=_NOW, end=_NOW + timedelta(hours=1)
        )
    )
    session.commit()
