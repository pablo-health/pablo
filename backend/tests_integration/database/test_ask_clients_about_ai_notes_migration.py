# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.ask_clients_about_ai_notes is on for every practice.

Two ways a practice gets the column, and both must land on "on":

* an **existing** practice, in a database that predates the revision, gets it
  from ``e9a4c2b7d518`` — proven by building the schema at head, taking the
  column away and stamping the revision before it, which is exactly the shape a
  deployed database is in before the upgrade runs;
* a **new** practice gets it from the column default, inserted without an
  opinion.

And the setting persists when turned off and back on.

Requires ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres`` and a role with
``CREATEDB``. Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from app.db.platform_models import PracticeRow
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from . import scratch_db

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REVISION = "e9a4c2b7d518"
_BEFORE = "c5d1e8a7b204"


def _alembic(database_url: str, *args: str) -> None:
    """Run the platform chain in a subprocess; see ``test_platform_chain._alembic``."""
    env = {**os.environ, "DATABASE_URL": database_url, "DATABASE_BACKEND": "postgres"}
    result = subprocess.run(  # noqa: S603 (trusted: this interpreter, test-controlled args)
        [sys.executable, "-m", "alembic", "-n", "platform", *args],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic {' '.join(args)} failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def platform_db() -> Iterator[str]:
    """A throwaway database with the platform chain at head."""
    db = scratch_db.scratch_name("pablo_ask_ai_notes")
    admin = create_engine(_db_url, isolation_level="AUTOCOMMIT")
    try:
        scratch_db.create(admin, db)
        url = scratch_db.swap_database(_db_url, db)
        _alembic(url, "upgrade", "head")
        yield url
    finally:
        scratch_db.drop(admin, db)
        admin.dispose()


def _insert_practice(engine: Engine, practice_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices"
                " (id, name, schema_name, owner_email, product, status, is_active, created_at)"
                " VALUES (:id, 'A practice', :schema, 'owner@example.com',"
                "         'pablo', 'active', TRUE, :ts)"
            ),
            {"id": practice_id, "schema": f"practice_{practice_id}", "ts": datetime.now(UTC)},
        )


def _asks(engine: Engine, practice_id: str) -> object:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT ask_clients_about_ai_notes FROM platform.practices WHERE id = :id"),
            {"id": practice_id},
        ).scalar_one()


def test_existing_practices_are_on_after_the_upgrade(platform_db: str) -> None:
    engine = create_engine(platform_db)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE platform.practices DROP COLUMN ask_clients_about_ai_notes")
            )
        _alembic(platform_db, "stamp", _BEFORE)
        _insert_practice(engine, "existing1")

        _alembic(platform_db, "upgrade", _REVISION)

        assert _asks(engine, "existing1") is True
    finally:
        engine.dispose()


def test_new_practices_are_on_and_the_setting_persists(platform_db: str) -> None:
    engine = create_engine(platform_db)
    try:
        _insert_practice(engine, "new1")
        assert _asks(engine, "new1") is True

        with Session(engine) as session:
            practice = session.get(PracticeRow, "new1")
            assert practice is not None
            practice.ask_clients_about_ai_notes = False
            session.commit()
        assert _asks(engine, "new1") is False

        with Session(engine) as session:
            practice = session.get(PracticeRow, "new1")
            assert practice is not None
            practice.ask_clients_about_ai_notes = True
            session.commit()
        assert _asks(engine, "new1") is True
    finally:
        engine.dispose()
