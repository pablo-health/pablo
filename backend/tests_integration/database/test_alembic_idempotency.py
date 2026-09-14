# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Regression: alembic upgrade must be idempotent against drifted DBs.

Migration ``f1c8d4a92b65`` (v0.9.3.10) failed on ``pablohealth-dev``
with ``DuplicateTable`` because ``backend/alembic/env.py`` used to call
``PlatformBase.metadata.create_all(connection)`` *before* alembic ran.
That pre-created ``platform.practices.is_pentest`` and
``platform.platform_audit_logs`` from the ORM model, so the migration
had to skip already-present objects to land at all.

That call is gone — the platform schema is built by its own chain from a
captured template — but the requirement it created has outlived it and is
still worth testing. Every existing database was built the old way, so
the migrations that ran against a ``create_all``-built schema have to stay
able to, and ``IF NOT EXISTS`` is load-bearing in each of them.

These tests spin up a throwaway database with the platform schema at head
(the fixture runs that chain; the tenant chain cannot run without it),
then run ``alembic upgrade head`` in a subprocess so settings/env state is
fresh, and verify success.

There used to be a second test here that hand-built ``platform.practices``
and ``platform.platform_audit_logs`` on an otherwise empty database, to
reproduce a partially-migrated one. It is gone, and nothing is lost with
it: the platform chain now builds the whole schema before the tenant chain
runs, so "these objects already exist when the migration reaches them" is
no longer a case to simulate — it is the only case, exercised on every run
by the test below. The database that test used to construct can no longer
exist either, since the check in front of the tenant chain rejects a
platform schema holding two tables out of twenty.

Requires:
  - ``DATABASE_URL`` + ``DATABASE_BACKEND=postgres``
  - The configured user must have ``CREATEDB`` privilege

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, text

from . import scratch_db

if TYPE_CHECKING:
    from collections.abc import Iterator

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=("PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres."),
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]


def _swap_db(url: str, db_name: str) -> str:
    return scratch_db.swap_database(url, db_name)


@pytest.fixture
def fresh_db() -> Iterator[str]:
    """A unique throwaway database with the platform schema built and nothing else.

    The platform chain runs here because the tenant chain cannot run without it:
    the tenant chain declares foreign keys into ``platform.users`` and creates
    nothing in that schema itself. It used to be able to, because
    ``create_all`` sat in its ``env.py`` bootstrap — which is exactly the coupling
    the platform chain removed. Every caller now runs the platform chain first,
    and for this module the fixture is that caller.

    The drop does not terminate connections. Doing so needs superuser or
    pg_signal_backend, and the role this suite runs as is deliberately
    neither — see scratch_db for why that matters.
    """
    db = scratch_db.scratch_name("pablo_alembic_test")
    admin = create_engine(_db_url, isolation_level="AUTOCOMMIT")
    try:
        scratch_db.create(admin, db)
        url = _swap_db(_db_url, db)
        _alembic(url, "-n", "platform", "upgrade", "head")
        yield url
    finally:
        scratch_db.drop(admin, db)
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
    # ``sys.executable -m alembic`` rather than ``poetry run alembic``: in a git
    # worktree poetry resolves no environment and falls through to whatever
    # ``alembic`` is first on PATH, which on a machine with anaconda installed is
    # a Python 3.10 that dies importing ``app.db`` with ``TypeError: 'type'
    # object is not subscriptable``. Under pytest the running interpreter is
    # already the right one, in CI and in a worktree alike.
    #
    # Tenant chain only. The platform chain it depends on is already at head —
    # the ``fresh_db`` fixture runs it, for the reason given there.
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
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

    The platform chain has already built the platform tables, so alembic reaches
    ``f1c8d4a92b65`` with its objects in place — the same situation
    ``create_all`` used to create, and the same thing a non-idempotent migration
    would trip over with ``DuplicateColumn`` / ``DuplicateTable``.
    """
    _alembic_upgrade_head(fresh_db)


def _alembic(database_url: str, *args: str) -> None:
    """Run an arbitrary ``alembic`` command in a subprocess (see note above)."""
    env = {**os.environ, "DATABASE_URL": database_url, "DATABASE_BACKEND": "postgres"}
    result = subprocess.run(  # noqa: S603 (trusted: this interpreter, test-controlled args)
        [sys.executable, "-m", "alembic", *args],
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
