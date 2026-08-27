# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.edition against real PostgreSQL.

Proves the DB-level contract the ORM model and migration both declare:
every pre-existing/insert-without-an-opinion row lands on ``therapist``
with no backfill, and the CHECK constraint rejects anything outside
``{therapist, personal}`` — the constraint only exists in the database,
so it can't be proven by the unit suite.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.db import PLATFORM_SCHEMA
from app.db.platform_models import PlatformBase, PracticeRow
from app.models.enums import PracticeEdition
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    db_url = os.environ["DATABASE_URL"]
    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {PLATFORM_SCHEMA}"))
        PlatformBase.metadata.create_all(conn)
    yield eng
    eng.dispose()


@pytest.fixture
def pg_session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    # booking_links.practice_id references practices, so practices can no
    # longer be truncated alone. Both tables are named explicitly rather than
    # using CASCADE: this stays an exact list of what the fixture resets, and
    # a future FK to practices fails loudly here instead of silently widening
    # the wipe. (Same multi-table form as the suite conftest.)
    session.execute(text("TRUNCATE TABLE platform.booking_links, platform.practices"))
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _practice_id() -> str:
    return f"test-practice-{uuid.uuid4().hex[:8]}"


def test_row_inserted_without_an_edition_defaults_to_therapist(pg_session: Session) -> None:
    practice_id = _practice_id()
    pg_session.execute(
        text(
            "INSERT INTO platform.practices"
            " (id, name, schema_name, owner_email, product, status, is_active, created_at)"
            " VALUES (:id, 'Default Edition Practice', :schema, 'owner@example.com',"
            "         'pablo', 'active', TRUE, :ts)"
        ),
        {"id": practice_id, "schema": f"schema_{practice_id}", "ts": datetime.now(UTC)},
    )
    pg_session.commit()

    edition = pg_session.execute(
        text("SELECT edition FROM platform.practices WHERE id = :id"),
        {"id": practice_id},
    ).scalar_one()

    assert edition == PracticeEdition.THERAPIST.value


def test_orm_default_matches_db_default(pg_session: Session) -> None:
    practice_id = _practice_id()
    row = PracticeRow(
        id=practice_id,
        name="ORM Default Practice",
        schema_name=f"schema_{practice_id}",
        owner_email="owner@example.com",
        created_at=datetime.now(UTC),
    )
    pg_session.add(row)
    pg_session.commit()

    reloaded = pg_session.get(PracticeRow, practice_id)
    assert reloaded is not None
    assert reloaded.edition == PracticeEdition.THERAPIST.value


def test_unknown_edition_is_rejected_at_the_database_level(pg_session: Session) -> None:
    practice_id = _practice_id()
    insert = text(
        "INSERT INTO platform.practices"
        " (id, name, schema_name, owner_email, product, status, is_active,"
        "  created_at, edition)"
        " VALUES (:id, 'Bad Edition Practice', :schema, 'owner@example.com',"
        "         'pablo', 'active', TRUE, :ts, 'made_up_edition')"
    )
    params = {"id": practice_id, "schema": f"schema_{practice_id}", "ts": datetime.now(UTC)}

    with pytest.raises(IntegrityError):
        pg_session.execute(insert, params)


def test_personal_edition_is_accepted(pg_session: Session) -> None:
    practice_id = _practice_id()
    pg_session.execute(
        text(
            "INSERT INTO platform.practices"
            " (id, name, schema_name, owner_email, product, status, is_active,"
            "  created_at, edition)"
            " VALUES (:id, 'Personal Practice', :schema, 'owner@example.com',"
            "         'pablo', 'active', TRUE, :ts, 'personal')"
        ),
        {"id": practice_id, "schema": f"schema_{practice_id}", "ts": datetime.now(UTC)},
    )
    pg_session.commit()

    edition = pg_session.execute(
        text("SELECT edition FROM platform.practices WHERE id = :id"),
        {"id": practice_id},
    ).scalar_one()

    assert edition == PracticeEdition.PERSONAL.value
