# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``create_practice_schema`` when the lock connection is lost mid-build.

The provisioning lock is held on its own connection, which sits idle while
the build runs on others. In production that idle connection was closed
under a build that went on to finish: every table, stamp and policy was in
place, but the release of the lock raised from ``finally`` and the practice
was recorded as failed.

These tests reproduce that by terminating the lock holder's backend from a
post-provision hook, which runs in the middle of the build, and check that
a finished build is reported as finished, that a build which really failed
still reports its own error, and that the lock is free afterwards either way.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import provisioning
from sqlalchemy import create_engine, text

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

# A session-level advisory lock on one bigint key is listed in pg_locks with
# the key's high half in classid and its low half in objid, objsubid = 1.
_LOCK_HOLDERS_SQL = text(
    "SELECT pid FROM pg_locks "
    "WHERE locktype = 'advisory' AND granted AND objsubid = 1 "
    "AND objid::bigint = (hashtext(:s)::bigint & 4294967295) "
    "AND pid <> pg_backend_pid()"
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture
def schema(engine: Engine) -> Iterator[str]:
    name = f"practice_test_lock_lost_{uuid.uuid4().hex[:8]}"
    saved_hooks = list(provisioning._post_provision_hooks)
    yield name
    provisioning._post_provision_hooks[:] = saved_hooks
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        conn.commit()


def _terminate_lock_holder(engine: Engine, schema_name: str) -> None:
    with engine.connect() as conn:
        pids = conn.execute(_LOCK_HOLDERS_SQL, {"s": schema_name}).scalars().all()
        assert len(pids) == 1, f"expected one backend holding the lock, found {pids}"
        terminated = conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pids[0]})
        assert terminated.scalar() is True
        conn.commit()


def _lock_is_free(engine: Engine, schema_name: str) -> bool:
    with engine.connect() as conn:
        got = conn.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:s))"), {"s": schema_name}
        ).scalar()
        if got:
            conn.execute(text("SELECT pg_advisory_unlock(hashtext(:s))"), {"s": schema_name})
        conn.commit()
    return bool(got)


def test_finished_build_succeeds_when_lock_connection_was_closed(
    engine: Engine, schema: str
) -> None:
    provisioning.register_post_provision_hook(_terminate_lock_holder)

    provisioning.create_practice_schema(engine, schema)

    assert provisioning._has_alembic_version(engine, schema)
    assert _lock_is_free(engine, schema)


def test_build_error_is_not_replaced_by_the_lock_release(engine: Engine, schema: str) -> None:
    class BuildFailedError(RuntimeError):
        pass

    def terminate_then_fail(eng: Engine, schema_name: str) -> None:
        _terminate_lock_holder(eng, schema_name)
        raise BuildFailedError(schema_name)

    provisioning.register_post_provision_hook(terminate_then_fail)

    with pytest.raises(BuildFailedError):
        provisioning.create_practice_schema(engine, schema)

    assert _lock_is_free(engine, schema)
