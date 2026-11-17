# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""platform.practices.provisioning_status accepts 'pending' and 'archived'.

``e4b9c2d71a63`` widens ``practices_provisioning_status_chk``. Proven against
the real chain in a throwaway database:

* at head the CHECK takes both new values and still refuses one it does not
  know;
* the downgrade refuses while a row holds a new value, rather than rewriting
  it, and goes through once no row does.

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
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

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
_REVISION = "e4b9c2d71a63"
_BEFORE = "c5d1e8a27f43"


def _alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the platform chain in a subprocess; see ``test_platform_chain._alembic``."""
    env = {**os.environ, "DATABASE_URL": database_url, "DATABASE_BACKEND": "postgres"}
    return subprocess.run(  # noqa: S603 (trusted: this interpreter, test-controlled args)
        [sys.executable, "-m", "alembic", "-n", "platform", *args],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _alembic_ok(database_url: str, *args: str) -> None:
    result = _alembic(database_url, *args)
    if result.returncode != 0:
        pytest.fail(
            f"alembic {' '.join(args)} failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
def platform_db() -> Iterator[str]:
    """A throwaway database with the platform chain at head."""
    db = scratch_db.scratch_name("pablo_pending_status")
    admin = create_engine(_db_url, isolation_level="AUTOCOMMIT")
    try:
        scratch_db.create(admin, db)
        url = scratch_db.swap_database(_db_url, db)
        _alembic_ok(url, "upgrade", "head")
        yield url
    finally:
        scratch_db.drop(admin, db)
        admin.dispose()


def _insert_practice(engine: Engine, practice_id: str, provisioning_status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices"
                " (id, name, schema_name, owner_email, product, status, is_active,"
                "  created_at, provisioning_status)"
                " VALUES (:id, 'A practice', :schema, 'owner@example.com',"
                "         'pablo', 'active', TRUE, :ts, :ps)"
            ),
            {
                "id": practice_id,
                "schema": f"practice_{practice_id}",
                "ts": datetime.now(UTC),
                "ps": provisioning_status,
            },
        )


def test_head_accepts_pending_and_archived_and_refuses_an_unknown_value(
    platform_db: str,
) -> None:
    engine = create_engine(platform_db)
    try:
        _insert_practice(engine, "pend1", "pending")
        _insert_practice(engine, "arch1", "archived")
        for known in ("in_progress", "ready", "failed"):
            _insert_practice(engine, f"k{known}", known)

        with pytest.raises(IntegrityError, match="practices_provisioning_status_chk"):
            _insert_practice(engine, "bogus1", "hibernating")
    finally:
        engine.dispose()


def test_downgrade_refuses_while_a_row_is_pending_and_succeeds_once_none_is(
    platform_db: str,
) -> None:
    engine = create_engine(platform_db)
    try:
        _insert_practice(engine, "pend2", "pending")

        refused = _alembic(platform_db, "downgrade", _BEFORE)
        assert refused.returncode != 0
        assert "'pending' or 'archived'" in refused.stderr
        # The row is untouched, and the chain is still at head.
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT provisioning_status FROM platform.practices WHERE id = 'pend2'")
                ).scalar_one()
                == "pending"
            )
            assert (
                conn.execute(
                    text("SELECT version_num FROM platform.alembic_version_platform")
                ).scalar_one()
                == _REVISION
            )

        with engine.begin() as conn:
            conn.execute(text("DELETE FROM platform.practices WHERE id = 'pend2'"))
        _alembic_ok(platform_db, "downgrade", _BEFORE)

        # The older CHECK is back: 'pending' is refused again.
        with pytest.raises(IntegrityError, match="practices_provisioning_status_chk"):
            _insert_practice(engine, "pend3", "pending")
        _insert_practice(engine, "ready3", "ready")
    finally:
        engine.dispose()
