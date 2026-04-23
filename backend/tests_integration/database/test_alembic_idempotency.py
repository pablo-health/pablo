# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression test for migration f1c8d4a92b65 idempotency (pa-0rx).

PR #68 shipped the platform-audit + is_pentest migration with plain
CREATE TABLE / ADD COLUMN DDL. On pablohealth-dev the platform_audit_logs
table and practices.is_pentest column already existed from earlier
hand-rolled DDL, so `alembic upgrade head` failed with DuplicateTable
and blocked the SaaS deploy. This test simulates that drift and asserts
the migration now upgrades cleanly.

Requires a local Postgres (make db-up) with CREATEDB privilege on the
configured DATABASE_URL role. Skipped otherwise so unit-test CI stays
green.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres (make db-up)."
    ),
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
PRIOR_REVISION = "e8f2a9c1b043"


def _url_for(database: str) -> str:
    return str(make_url(_db_url).set(database=database))


def _create_db(name: str) -> None:
    admin = create_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        admin.dispose()


def _drop_db(name: str) -> None:
    admin = create_engine(_url_for("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        admin.dispose()


def _run_alembic(database_url: str, target: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    env["DATABASE_BACKEND"] = "postgres"
    return subprocess.run(
        ["poetry", "run", "alembic", "upgrade", target],
        cwd=str(BACKEND_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_f1c8d4a92b65_is_idempotent_on_drifted_db() -> None:
    """Pre-create target objects, then upgrade head — must not DuplicateTable."""
    db_name = f"pablo_idem_{uuid.uuid4().hex[:8]}"
    test_url = _url_for(db_name)

    _create_db(db_name)
    try:
        prior = _run_alembic(test_url, PRIOR_REVISION)
        assert prior.returncode == 0, (
            f"upgrade to {PRIOR_REVISION} failed:\n"
            f"stdout={prior.stdout}\nstderr={prior.stderr}"
        )

        # Simulate the pablohealth-dev drift: both the target table and
        # the target column already exist when f1c8d4a92b65 runs.
        engine = create_engine(test_url)
        with engine.begin() as conn:
            conn.execute(
                text(
                    "ALTER TABLE platform.practices"
                    " ADD COLUMN IF NOT EXISTS is_pentest BOOLEAN"
                    " NOT NULL DEFAULT false"
                )
            )
            conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS"
                    " platform.platform_audit_logs ("
                    " id VARCHAR(128) PRIMARY KEY,"
                    " timestamp TIMESTAMPTZ NOT NULL,"
                    " expires_at TIMESTAMPTZ NOT NULL,"
                    " actor_user_id VARCHAR(128) NOT NULL,"
                    " action VARCHAR(50) NOT NULL,"
                    " resource_type VARCHAR(30) NOT NULL,"
                    " resource_id VARCHAR(128) NOT NULL,"
                    " tenant_schema VARCHAR(128),"
                    " ip_address VARCHAR(45),"
                    " user_agent TEXT,"
                    " details JSONB)"
                )
            )
        engine.dispose()

        head = _run_alembic(test_url, "head")
        assert head.returncode == 0, (
            "alembic upgrade head failed on drifted DB:\n"
            f"stdout={head.stdout}\nstderr={head.stderr}"
        )

        # Spot-check the expected end state — column, constraint, trigger,
        # and audit table are all in place after the idempotent upgrade.
        engine = create_engine(test_url)
        with engine.connect() as conn:
            assert conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns"
                    " WHERE table_schema='platform'"
                    " AND table_name='practices'"
                    " AND column_name='is_pentest'"
                )
            ).scalar() == 1
            assert conn.execute(
                text(
                    "SELECT 1 FROM pg_constraint"
                    " WHERE conname='practices_pentest_schema_name'"
                )
            ).scalar() == 1
            assert conn.execute(
                text(
                    "SELECT 1 FROM pg_trigger"
                    " WHERE tgname='practices_pentest_immutable'"
                )
            ).scalar() == 1
            assert conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables"
                    " WHERE table_schema='platform'"
                    " AND table_name='platform_audit_logs'"
                )
            ).scalar() == 1
        engine.dispose()
    finally:
        _drop_db(db_name)
