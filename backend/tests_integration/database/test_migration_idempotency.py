# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression test: f1c8d4a92b65 must be idempotent on drifted DBs.

PR #68 shipped a migration that assumed a clean starting state. Dev
already had ``platform.platform_audit_logs`` and the ``is_pentest``
column from earlier hand-rolled iterations, so ``alembic upgrade head``
died with DuplicateTable. This test pins that behavior: stamp to the
revision before f1c8d4a92b65, pre-create the objects the migration
would otherwise create, then upgrade head and expect success.

Requires:
  - ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``
  - ``platform.practices`` present (``make db-up && make db-migrate`` once)

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres; "
        "apply migrations with `make db-migrate`."
    ),
)

TARGET_REVISION = "f1c8d4a92b65"
PREVIOUS_REVISION = "e8f2a9c1b043"
VERSION_TABLE_SCHEMA = "practice"


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    return cfg


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    command.upgrade(_alembic_config(), "head")
    yield eng
    command.upgrade(_alembic_config(), "head")
    eng.dispose()


def _strip_migration_artifacts(engine: Engine) -> None:
    """Remove objects f1c8d4a92b65 adds that ``create_all`` won't re-provision.

    ``create_all`` (run by ``alembic env.py`` on every command) re-creates
    missing platform tables from the ORM model, so dropping
    ``platform_audit_logs`` isn't useful here — the table is ORM-managed.
    The column/constraint/trigger/function, however, are migration-only,
    so removing them plus stamping back produces the real drift scenario.
    """
    with engine.begin() as conn:
        conn.execute(
            text(
                "DROP TRIGGER IF EXISTS practices_pentest_immutable "
                "ON platform.practices"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE platform.practices "
                "DROP CONSTRAINT IF EXISTS practices_pentest_schema_name"
            )
        )
        conn.execute(
            text("ALTER TABLE platform.practices DROP COLUMN IF EXISTS is_pentest")
        )
        conn.execute(
            text("DROP FUNCTION IF EXISTS platform.practices_pentest_immutable()")
        )


def _current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT version_num FROM {VERSION_TABLE_SCHEMA}.alembic_version")
        ).scalar()


def _object_exists(engine: Engine, sql: str) -> bool:
    with engine.connect() as conn:
        return bool(conn.execute(text(sql)).scalar())


def test_upgrade_succeeds_when_platform_audit_logs_already_exists(
    engine: Engine,
) -> None:
    """The dev/prod drift that blocked deploy.yml: table pre-existing, migration must no-op the CREATE TABLE."""
    _strip_migration_artifacts(engine)
    # env.py's create_all re-provisions platform.platform_audit_logs when
    # any alembic command runs, so stamping back to the previous revision
    # leaves the table in place — exactly the drift state we need.
    command.stamp(_alembic_config(), PREVIOUS_REVISION)

    assert _object_exists(
        engine,
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'platform' AND table_name = 'platform_audit_logs'",
    ), "create_all should have provisioned platform_audit_logs"

    # Must NOT raise DuplicateTable / DuplicateColumn / DuplicateObject.
    command.upgrade(_alembic_config(), "head")

    assert _current_revision(engine) == TARGET_REVISION
    assert _object_exists(
        engine,
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'platform' "
        "AND table_name = 'practices' AND column_name = 'is_pentest'",
    )
    assert _object_exists(
        engine,
        "SELECT 1 FROM pg_trigger t "
        "JOIN pg_class c ON t.tgrelid = c.oid "
        "JOIN pg_namespace n ON c.relnamespace = n.oid "
        "WHERE n.nspname = 'platform' AND c.relname = 'practices' "
        "AND t.tgname = 'practices_pentest_immutable'",
    )
    assert _object_exists(
        engine,
        "SELECT 1 FROM pg_constraint "
        "WHERE conname = 'practices_pentest_schema_name'",
    )


def test_upgrade_is_noop_when_migration_already_applied(engine: Engine) -> None:
    """Full-drift: every artifact already exists; stamping back then upgrading must not raise."""
    # engine fixture already ran upgrade head. Rewind the version pointer only.
    command.stamp(_alembic_config(), PREVIOUS_REVISION)

    command.upgrade(_alembic_config(), "head")

    assert _current_revision(engine) == TARGET_REVISION
