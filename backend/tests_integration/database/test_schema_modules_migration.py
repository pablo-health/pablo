# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.schema_modules holds every module for every practice.

Two ways a practice gets the column, and both must land on every module:

* an **existing** practice, in a database that predates the revision, gets it
  from ``c4e8a2f6d1b9`` — proven by building the schema at head, taking the
  column away and stamping the revision before it, which is the shape a
  deployed database is in before the upgrade runs;
* a **new** practice gets it from the column default.

The core module cannot be left out, and the upgrade is safe to run twice.

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
from app.db.schema_modules import CLINICAL, MODULES, modules_for
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
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
_REVISION = "c4e8a2f6d1b9"
_BEFORE = "a3f8c1d6e924"


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
    db = scratch_db.scratch_name("pablo_schema_modules")
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


def _modules(engine: Engine, practice_id: str) -> object:
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT schema_modules FROM platform.practices WHERE id = :id"),
            {"id": practice_id},
        ).scalar_one()


def _drop_column(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE platform.practices DROP COLUMN schema_modules"))


def test_existing_practices_hold_every_module_after_the_upgrade(platform_db: str) -> None:
    engine = create_engine(platform_db)
    try:
        _drop_column(engine)
        _alembic(platform_db, "stamp", _BEFORE)
        _insert_practice(engine, "existing1")

        _alembic(platform_db, "upgrade", _REVISION)

        assert _modules(engine, "existing1") == list(MODULES)
    finally:
        engine.dispose()


def test_the_upgrade_runs_twice_without_complaint(platform_db: str) -> None:
    # The template already carries the column on a fresh install, so the
    # revision has to be a no-op over it.
    _alembic(platform_db, "stamp", _BEFORE)
    _alembic(platform_db, "upgrade", _REVISION)
    engine = create_engine(platform_db)
    try:
        with engine.connect() as conn:
            checks = conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint"
                    " WHERE conname = 'ck_practices_schema_modules_clinical'"
                )
            ).scalar_one()
        assert checks == 1
    finally:
        engine.dispose()


def test_a_new_practice_holds_every_module(platform_db: str) -> None:
    engine = create_engine(platform_db)
    try:
        _insert_practice(engine, "new1")
        assert _modules(engine, "new1") == list(MODULES)

        with Session(engine) as session:
            practice = session.get(PracticeRow, "new1")
            assert practice is not None
            assert modules_for(practice) == MODULES
    finally:
        engine.dispose()


def test_a_practice_can_hold_clinical_alone_but_not_without_it(platform_db: str) -> None:
    engine = create_engine(platform_db)
    try:
        _insert_practice(engine, "core1")
        with Session(engine) as session:
            practice = session.get(PracticeRow, "core1")
            assert practice is not None
            practice.schema_modules = [CLINICAL]
            session.commit()
        assert _modules(engine, "core1") == [CLINICAL]

        with (
            pytest.raises(IntegrityError, match="ck_practices_schema_modules_clinical"),
            engine.begin() as conn,
        ):
            conn.execute(
                text(
                    "UPDATE platform.practices SET schema_modules = ARRAY['ai', 'portal']"
                    " WHERE id = 'core1'"
                )
            )
        assert _modules(engine, "core1") == [CLINICAL]
    finally:
        engine.dispose()
