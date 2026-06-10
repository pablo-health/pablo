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


def _alembic(database_url: str, *args: str) -> None:
    """Run an arbitrary ``alembic`` command in a subprocess (see note above)."""
    env = {**os.environ, "DATABASE_URL": database_url, "DATABASE_BACKEND": "postgres"}
    result = subprocess.run(  # noqa: S603 (trusted: hardcoded poetry/alembic, test-controlled args)
        ["poetry", "run", "alembic", *args],  # noqa: S607
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


def _meets_criteria_is_nullable(database_url: str) -> bool:
    eng = create_engine(database_url)
    try:
        with eng.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns"
                    " WHERE table_schema = 'practice'"
                    " AND table_name = 'diagnostic_assessments'"
                    " AND column_name = 'meets_criteria'"
                )
            ).fetchone()
    finally:
        eng.dispose()
    assert row is not None, "practice.diagnostic_assessments.meets_criteria not found"
    return row[0] == "YES"


def test_meets_criteria_nullable_round_trip(fresh_db: str) -> None:
    """up/down/up for c4e8d1f6a2b9 (PABLO-6xj.8).

    Head makes ``diagnostic_assessments.meets_criteria`` nullable (checklist
    rows have no verdict); the downgrade backfills any NULLs to false and
    restores NOT NULL; the re-upgrade drops it again. Exercises the downgrade
    path, which the plain upgrade-head tests never touch.
    """
    _alembic(fresh_db, "upgrade", "head")
    assert _meets_criteria_is_nullable(fresh_db) is True

    _alembic(fresh_db, "downgrade", "b7e2f4a1c9d3")
    assert _meets_criteria_is_nullable(fresh_db) is False

    _alembic(fresh_db, "upgrade", "head")
    assert _meets_criteria_is_nullable(fresh_db) is True


_LEGACY_UID = "fXEv86J4bZhmzZntfOqEAQQB7M53"


def test_phase_c_converts_deployment_defined_user_fk_columns(fresh_db: str) -> None:
    """Phase C (c1d7e4a9f2b6) must flip EVERY column referencing users(id).

    A deployment can define extra tables (beyond this repo's models) whose
    columns FK to ``platform.users(id)``. The Phase-C FK snapshot drops
    those constraints before the cast — but the columns themselves must
    also flip to ``uuid`` (with the legacy-id remap) before the restore,
    or the re-add fails with "incompatible types: character varying and
    uuid". Reproduces a real deployment failure.
    """
    _alembic(fresh_db, "upgrade", "head")
    # Walk users.id (and friends) back to varchar, as a pre-Phase-C DB had.
    _alembic(fresh_db, "downgrade", "f4c1a9d3b7e2")

    eng = create_engine(fresh_db)
    try:
        with eng.begin() as conn:
            # A legacy account: users.id IS the Firebase uid, linked in
            # user_identities exactly as a4c91b6e3f08 backfilled it.
            conn.execute(
                text(
                    "INSERT INTO platform.users"
                    " (id, email, name, created_at, status, is_platform_admin,"
                    "  chat_quality_review_opt_in, session_notes_quality_review_opt_in)"
                    " VALUES (:uid, 'legacy@example.test', 'Legacy User', now(),"
                    "         'approved', false, false, false)"
                ),
                {"uid": _LEGACY_UID},
            )
            conn.execute(
                text(
                    "INSERT INTO platform.user_identities"
                    " (provider, subject_id, user_id, linked_at)"
                    " VALUES ('firebase', :uid, :uid, now())"
                ),
                {"uid": _LEGACY_UID},
            )
            # A table this repo knows nothing about, referencing users(id).
            conn.execute(
                text(
                    """
                    CREATE TABLE platform.ext_upload_log (
                        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                        uploaded_by VARCHAR(128),
                        CONSTRAINT ext_upload_log_uploaded_by_fkey
                            FOREIGN KEY (uploaded_by)
                            REFERENCES platform.users(id) ON DELETE SET NULL
                    )
                    """
                )
            )
            conn.execute(
                text("INSERT INTO platform.ext_upload_log (uploaded_by) VALUES (:uid)"),
                {"uid": _LEGACY_UID},
            )
    finally:
        eng.dispose()

    _alembic(fresh_db, "upgrade", "head")

    eng = create_engine(fresh_db)
    try:
        with eng.connect() as conn:
            col_type = conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns"
                    " WHERE table_schema = 'platform'"
                    " AND table_name = 'ext_upload_log'"
                    " AND column_name = 'uploaded_by'"
                )
            ).scalar_one()
            fk_count = conn.execute(
                text(
                    "SELECT count(*) FROM pg_constraint"
                    " WHERE conname = 'ext_upload_log_uploaded_by_fkey'"
                )
            ).scalar_one()
            # The legacy uid was remapped to the user's new uuid id.
            row = conn.execute(
                text(
                    "SELECT e.uploaded_by::text, u.id::text"
                    " FROM platform.ext_upload_log e"
                    " JOIN platform.user_identities ui"
                    "   ON ui.provider = 'firebase' AND ui.subject_id = :uid"
                    " JOIN platform.users u ON u.id = ui.user_id"
                    " LIMIT 1"
                ),
                {"uid": _LEGACY_UID},
            ).fetchone()
    finally:
        eng.dispose()

    assert col_type == "uuid"
    assert fk_count == 1
    assert row is not None
    uploaded_by, new_user_id = row
    assert uploaded_by == new_user_id
    assert uploaded_by != _LEGACY_UID


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
