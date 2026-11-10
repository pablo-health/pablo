# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for the place of service an appointment type learns from bookings.

The route tests prove the rule over the in-memory repository; these prove the
column the template and the revision add: that the value round-trips, that
recording it is per clinician, that it does not touch ``updated_at`` (it is
learned, not edited), and that the CHECK refuses anything but 11, 02 and 10.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.db import _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.repositories.postgres.appointment_type import PostgresAppointmentTypeRepository
from app.scheduling_engine.models.appointment_type import AppointmentType
from sqlalchemy import text
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
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = f"practice_test_type_place_{uuid.uuid4().hex[:8]}"
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


def _clinician(session: Session) -> str:
    user_id = str(uuid.uuid4())
    session.commit()
    arm_current_user_id(session, user_id)
    return user_id


def _type(user_id: str, name: str = "Session") -> AppointmentType:
    return AppointmentType(
        id=str(uuid.uuid4()), user_id=user_id, name=name, created_at=_NOW, updated_at=_NOW
    )


def test_a_new_type_has_never_been_booked(session: Session) -> None:
    user_id = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    created = repo.create(_type(user_id))

    found = repo.get(created.id, user_id)

    assert found is not None
    assert found.last_place_of_service is None


def test_recording_round_trips_and_leaves_updated_at_alone(session: Session) -> None:
    user_id = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    created = repo.create(_type(user_id))

    repo.record_place(created.id, user_id, "10")
    session.commit()
    session.expire_all()
    arm_current_user_id(session, user_id)

    found = repo.get(created.id, user_id)
    assert found is not None
    assert found.last_place_of_service == "10"
    assert found.modality == "video"
    assert found.updated_at == _NOW

    repo.record_place(created.id, user_id, "11")
    found = repo.get(created.id, user_id)
    assert found is not None
    assert found.last_place_of_service == "11"


def test_another_clinician_s_same_named_type_is_unaffected(session: Session) -> None:
    user_a = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    theirs = repo.create(_type(user_a))
    session.commit()

    user_b = _clinician(session)
    mine = repo.create(_type(user_b))
    repo.record_place(mine.id, user_b, "02")
    repo.record_place(theirs.id, user_b, "02")
    session.commit()

    arm_current_user_id(session, user_a)
    found = repo.get(theirs.id, user_a)
    assert found is not None
    assert found.last_place_of_service is None


def test_an_editor_update_keeps_what_was_learned(session: Session) -> None:
    user_id = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    created = repo.create(_type(user_id))
    repo.record_place(created.id, user_id, "10")

    edited = repo.get(created.id, user_id)
    assert edited is not None
    edited.duration_minutes = 60
    repo.update(edited)

    found = repo.get(created.id, user_id)
    assert found is not None
    assert found.last_place_of_service == "10"


def test_the_column_refuses_anything_but_the_three_codes(session: Session) -> None:
    user_id = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    created = repo.create(_type(user_id))
    session.commit()

    with pytest.raises(IntegrityError, match="ck_appointment_types_last_place_of_service"):
        session.execute(
            text("UPDATE appointment_types SET last_place_of_service = '12' WHERE id = :id"),
            {"id": created.id},
        )
