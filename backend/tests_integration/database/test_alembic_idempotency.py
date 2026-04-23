# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression: alembic upgrade must be idempotent against drifted DBs.

Migration ``f1c8d4a92b65`` (v0.9.3.10) failed on ``pablohealth-dev``
with ``DuplicateTable`` because ``backend/alembic/env.py`` calls
``PlatformBase.metadata.create_all(connection)`` *before* alembic runs.
That pre-creates ``platform.practices.is_pentest`` and
``platform.platform_audit_logs`` from the ORM model — the migration
must skip already-present objects so it can land cleanly on dev/prod
DBs that already have them.

These tests spin up a throwaway database, run ``alembic upgrade head``
in a subprocess (so settings/env state is fresh), and verify success.

Requires:
  - ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``
  - The configured user must have ``CREATEDB`` privilege

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=("PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres."),
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _swap_db(url: str, db_name: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{db_name}"


@pytest.fixture
def fresh_db() -> Iterator[str]:
    """Create a unique throwaway database; drop it after the test."""
    db = f"pablo_alembic_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(_db_url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{db}"'))
        yield _swap_db(_db_url, db)
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = :db AND pid <> pg_backend_pid()"
                ),
                {"db": db},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{db}"'))
        admin.dispose()


def _alembic_upgrade_head(database_url: str) -> None:
    """Run ``alembic upgrade head`` in a subprocess against ``database_url``.

    Subprocess isolation matters: ``backend/alembic/env.py`` reads
    ``settings.database_url`` at import time, so an in-process call
    would reuse the cached URL from the first run.
    """
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "DATABASE_BACKEND": "postgres",
    }
    # poetry from PATH is fine in tests; no untrusted input here.
    result = subprocess.run(
        ["poetry", "run", "alembic", "upgrade", "head"],  # noqa: S607
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"alembic upgrade head failed.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


def test_upgrade_head_succeeds_on_fresh_db(fresh_db: str) -> None:
    """Fresh DB → ``alembic upgrade head`` succeeds.

    ``env.py`` runs ``PlatformBase.metadata.create_all`` before
    migrations, so the platform tables exist *before* alembic gets to
    ``f1c8d4a92b65``. A non-idempotent migration would raise
    ``DuplicateColumn`` / ``DuplicateTable`` here.
    """
    _alembic_upgrade_head(fresh_db)


def test_upgrade_idempotent_after_simulated_drift(fresh_db: str) -> None:
    """Pre-create the conflicting platform objects exactly as a partial
    prior run would have left them, then upgrade head."""
    eng = create_engine(fresh_db)
    try:
        with eng.begin() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS platform"))
            conn.execute(
                text(
                    """
                    CREATE TABLE platform.practices (
                        id VARCHAR(128) PRIMARY KEY,
                        name VARCHAR(255) NOT NULL,
                        schema_name VARCHAR(128) UNIQUE NOT NULL,
                        tenant_id VARCHAR(128) UNIQUE,
                        owner_email VARCHAR(255) NOT NULL,
                        owner_user_id VARCHAR(128) DEFAULT '',
                        product VARCHAR(20) DEFAULT 'pablo',
                        status VARCHAR(20) DEFAULT 'active',
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        is_pentest BOOLEAN NOT NULL DEFAULT false
                    )
                    """
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE platform.platform_audit_logs (
                        id VARCHAR(128) PRIMARY KEY,
                        timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
                        expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
                        actor_user_id VARCHAR(128) NOT NULL,
                        action VARCHAR(50) NOT NULL,
                        resource_type VARCHAR(30) NOT NULL,
                        resource_id VARCHAR(128) NOT NULL,
                        tenant_schema VARCHAR(128),
                        ip_address VARCHAR(45),
                        user_agent TEXT,
                        details JSONB
                    )
                    """
                )
            )
    finally:
        eng.dispose()
    _alembic_upgrade_head(fresh_db)
