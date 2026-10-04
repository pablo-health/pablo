# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for appointment type name collisions.

``uq_appointment_types_user_name`` keeps one clinician from having two types
with the same name. The repository turns that constraint's violation into
``AppointmentTypeNameTakenError`` inside a SAVEPOINT, so these tests prove
three things only a real database can: the constraint actually fires, the
translation names it correctly, and the session is still usable afterwards
with earlier work intact.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.repositories.postgres.appointment_type import PostgresAppointmentTypeRepository
from app.scheduling_engine.exceptions import AppointmentTypeNameTakenError
from app.scheduling_engine.models.appointment_type import AppointmentType
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

    schema = f"practice_test_type_name_{uuid.uuid4().hex[:8]}"
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


def _type(user_id: str, name: str) -> AppointmentType:
    return AppointmentType(
        id=str(uuid.uuid4()),
        user_id=user_id,
        name=name,
        duration_minutes=50,
        created_at=_NOW,
        updated_at=_NOW,
    )


def test_creating_a_second_type_with_a_taken_name_is_refused(session: Session) -> None:
    user_id = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    first = repo.create(_type(user_id, "Consultation"))

    with pytest.raises(AppointmentTypeNameTakenError) as exc_info:
        repo.create(_type(user_id, "Consultation"))

    assert exc_info.value.name == "Consultation"
    # Only the refused insert was undone: the session still works and the
    # first type, written earlier in the same transaction, is still there.
    assert [t.id for t in repo.list_by_user(user_id)] == [first.id]


def test_renaming_onto_a_taken_name_is_refused_and_keeps_the_old_name(
    session: Session,
) -> None:
    user_id = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    repo.create(_type(user_id, "Consultation"))
    intake = repo.create(_type(user_id, "Intake"))

    intake.name = "Consultation"
    with pytest.raises(AppointmentTypeNameTakenError):
        repo.update(intake)

    names = sorted(t.name for t in repo.list_by_user(user_id))
    assert names == ["Consultation", "Intake"]


def test_the_same_name_is_free_for_a_different_clinician(session: Session) -> None:
    user_a = _clinician(session)
    repo = PostgresAppointmentTypeRepository(session)
    repo.create(_type(user_a, "Consultation"))
    session.commit()

    user_b = _clinician(session)
    created = repo.create(_type(user_b, "Consultation"))

    assert [t.id for t in repo.list_by_user(user_b)] == [created.id]
