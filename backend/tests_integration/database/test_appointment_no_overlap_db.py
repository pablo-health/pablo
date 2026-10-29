# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for the appointments no-overlap exclusion constraint.

``SchedulingService._reject_if_overlapping`` is a check-then-insert: two
requests that both pass the check before either commits both insert, and
the clinician is double-booked. ``uq_appointments_user_start_active`` only
catches the pair that share a start. ``ex_appointments_user_time_active``
is the backstop for every other overlap: these tests race two bookings on
one calendar through separate sessions and prove exactly one lands, that
the loser gets the same ``AppointmentConflictError`` the check raises, and
that the constraint's scope matches ``list_overlapping``'s — back-to-back
is fine, a cancelled appointment holds nothing.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.db import (
    _current_tenant_schema,
    arm_current_user_id,
    set_tenant_schema,
)
from app.models import Patient
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.scheduling_engine.exceptions import AppointmentConflictError
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.services.scheduling import SchedulingService
from sqlalchemy import text
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


def _at(hour: int, minute: int) -> datetime:
    return _NOW.replace(hour=hour, minute=minute)


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = f"practice_test_appt_no_overlap_{uuid.uuid4().hex[:8]}"
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
        created_at=_NOW,
        updated_at=_NOW,
    )


def _booking_data(patient_id: str, start: datetime, end: datetime) -> dict[str, object]:
    return {
        "patient_id": patient_id,
        "title": "Session",
        "start_at": start,
        "end_at": end,
        "duration_minutes": int((end - start).total_seconds() // 60),
    }


def test_two_racing_overlapping_bookings_land_exactly_one(
    engine: Engine, tenant_schema: str, session: Session
) -> None:
    """10:00-10:50 and 10:30-11:20 on one calendar, each in its own session,
    both past the overlap check before either commits: one commits, the
    other gets the conflict error the check would have raised.
    """
    user_id = str(uuid.uuid4())
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    session.commit()

    windows = [(_at(10, 0), _at(10, 50)), (_at(10, 30), _at(11, 20))]
    checked = threading.Barrier(2)
    outcomes: dict[int, str] = {}

    def book(index: int, start: datetime, end: datetime) -> None:
        sess = Session(bind=engine)
        try:
            set_tenant_schema(sess, tenant_schema)
            arm_current_user_id(sess, user_id)
            service = SchedulingService(PostgresAppointmentRepository(sess))
            # Both requests see an empty calendar here — this is the gap the
            # constraint closes.
            service.reject_if_taken(user_id, start, end)
            checked.wait()
            try:
                service.create_appointment(user_id, data=_booking_data(patient_id, start, end))
                sess.commit()
                outcomes[index] = "booked"
            except AppointmentConflictError:
                sess.rollback()
                outcomes[index] = "conflict"
        finally:
            sess.close()
            _current_tenant_schema.set(None)

    threads = [
        threading.Thread(target=book, args=(i, start, end))
        for i, (start, end) in enumerate(windows)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes.values()) == ["booked", "conflict"]
    set_tenant_schema(session, tenant_schema)
    arm_current_user_id(session, user_id)
    booked = PostgresAppointmentRepository(session).list_overlapping(user_id, _at(9, 0), _at(12, 0))
    assert len(booked) == 1


def test_back_to_back_appointments_do_not_collide(session: Session) -> None:
    user_id = str(uuid.uuid4())
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    repo = PostgresAppointmentRepository(session)
    repo.create(
        _make_appointment(user_id=user_id, patient_id=patient_id, start=_at(10, 0), end=_at(10, 50))
    )
    session.commit()

    repo.create(
        _make_appointment(
            user_id=user_id, patient_id=patient_id, start=_at(10, 50), end=_at(11, 40)
        )
    )
    session.commit()


def test_overlap_with_cancelled_appointment_is_allowed(session: Session) -> None:
    user_id = str(uuid.uuid4())
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    repo = PostgresAppointmentRepository(session)
    repo.create(
        _make_appointment(
            user_id=user_id,
            patient_id=patient_id,
            start=_at(10, 0),
            end=_at(10, 50),
            status="cancelled",
        )
    )
    session.commit()

    repo.create(
        _make_appointment(
            user_id=user_id, patient_id=patient_id, start=_at(10, 30), end=_at(11, 20)
        )
    )
    session.commit()


def test_partial_overlap_raises_the_conflict_error(session: Session) -> None:
    """Straight through the repository, past the service's check: the
    constraint alone turns the second insert into ``AppointmentConflictError``
    and leaves the session usable.
    """
    user_id = str(uuid.uuid4())
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    repo = PostgresAppointmentRepository(session)
    repo.create(
        _make_appointment(user_id=user_id, patient_id=patient_id, start=_at(10, 0), end=_at(10, 50))
    )
    session.commit()

    with pytest.raises(AppointmentConflictError):
        repo.create(
            _make_appointment(
                user_id=user_id, patient_id=patient_id, start=_at(10, 30), end=_at(11, 20)
            )
        )
    # The SAVEPOINT undid only the failed insert; the calendar still works.
    assert len(repo.list_overlapping(user_id, _at(9, 0), _at(12, 0))) == 1


def test_different_calendars_may_overlap(session: Session) -> None:
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    _arm(session, user_a)
    patient_a = _seed_patient(session, user_a)
    PostgresAppointmentRepository(session).create(
        _make_appointment(user_id=user_a, patient_id=patient_a, start=_at(10, 0), end=_at(10, 50))
    )
    session.commit()

    _arm(session, user_b)
    patient_b = _seed_patient(session, user_b)
    PostgresAppointmentRepository(session).create(
        _make_appointment(user_id=user_b, patient_id=patient_b, start=_at(10, 30), end=_at(11, 20))
    )
    session.commit()
