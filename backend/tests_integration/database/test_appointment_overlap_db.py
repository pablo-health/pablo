# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for ``PostgresAppointmentRepository.list_overlapping``.

The unit suite (``tests/test_appointment_overlap.py``) proves the same
scenarios against the in-memory repository; these tests prove the SQL
predicate behind ``list_overlapping`` matches it exactly against a
provisioned tenant schema, including the case ``list_by_range``'s
``start_at``-only filter cannot see: an appointment that starts before the
proposed window and ends inside it.

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

    schema = f"practice_test_appt_overlap_{uuid.uuid4().hex[:8]}"
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


def _seed_appointment(  # noqa: PLR0913 — seeding helper with independent start/end/status knobs
    session: Session,
    *,
    user_id: str,
    patient_id: str,
    start: datetime,
    end: datetime,
    status: str = "confirmed",
) -> str:
    appointment_id = str(uuid.uuid4())
    PostgresAppointmentRepository(session).create(
        Appointment(
            id=appointment_id,
            user_id=user_id,
            patient_id=patient_id,
            title="Session",
            start_at=start,
            end_at=end,
            duration_minutes=int((end - start).total_seconds() // 60),
            status=status,
            session_type="individual",
        )
    )
    return appointment_id


def test_exact_same_start_and_end_collides(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    _seed_appointment(
        session, user_id=user_id, patient_id=patient_id, start=_NOW, end=_NOW + timedelta(hours=1)
    )
    session.commit()

    _arm(session, user_id)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_id, _NOW, _NOW + timedelta(hours=1)
    )
    assert len(result) == 1


def test_existing_fully_contains_proposed_slot_collides(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    _seed_appointment(
        session,
        user_id=user_id,
        patient_id=patient_id,
        start=_NOW - timedelta(hours=1),
        end=_NOW + timedelta(hours=2),
    )
    session.commit()

    _arm(session, user_id)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_id, _NOW, _NOW + timedelta(hours=1)
    )
    assert len(result) == 1


def test_proposed_slot_fully_contains_existing_collides(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    _seed_appointment(
        session,
        user_id=user_id,
        patient_id=patient_id,
        start=_NOW + timedelta(minutes=15),
        end=_NOW + timedelta(minutes=45),
    )
    session.commit()

    _arm(session, user_id)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_id, _NOW, _NOW + timedelta(hours=1)
    )
    assert len(result) == 1


def test_existing_starts_before_window_and_ends_inside_collides(session: Session) -> None:
    """The case ``list_by_range`` misses — it filters on ``start_at`` only."""
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    _seed_appointment(
        session,
        user_id=user_id,
        patient_id=patient_id,
        start=_NOW - timedelta(minutes=30),
        end=_NOW + timedelta(minutes=30),
    )
    session.commit()

    _arm(session, user_id)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_id, _NOW, _NOW + timedelta(hours=1)
    )
    assert len(result) == 1


def test_back_to_back_does_not_collide(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    _seed_appointment(
        session, user_id=user_id, patient_id=patient_id, start=_NOW - timedelta(hours=1), end=_NOW
    )
    session.commit()

    _arm(session, user_id)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_id, _NOW, _NOW + timedelta(hours=1)
    )
    assert result == []


def test_cancelled_appointment_does_not_collide(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    _seed_appointment(
        session,
        user_id=user_id,
        patient_id=patient_id,
        start=_NOW,
        end=_NOW + timedelta(hours=1),
        status="cancelled",
    )
    session.commit()

    _arm(session, user_id)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_id, _NOW, _NOW + timedelta(hours=1)
    )
    assert result == []


def test_exclude_appointment_id_omits_that_appointment(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    moving_id = _seed_appointment(
        session, user_id=user_id, patient_id=patient_id, start=_NOW, end=_NOW + timedelta(hours=1)
    )
    session.commit()

    _arm(session, user_id)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_id, _NOW, _NOW + timedelta(hours=1), exclude_appointment_id=moving_id
    )
    assert result == []


def test_different_user_id_not_returned(session: Session) -> None:
    user_a, user_b = _user(), _user()
    _arm(session, user_b)
    patient_b = _seed_patient(session, user_b)
    _seed_appointment(
        session, user_id=user_b, patient_id=patient_b, start=_NOW, end=_NOW + timedelta(hours=1)
    )
    session.commit()

    _arm(session, user_a)
    result = PostgresAppointmentRepository(session).list_overlapping(
        user_a, _NOW, _NOW + timedelta(hours=1)
    )
    assert result == []
