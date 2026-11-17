# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Practice walkers skip a practice whose schema is not ready.

A practice can be registered with no schema yet (``pending``), have its schema
stored outside the database and dropped (``archived``), or be part-way through
provisioning (``in_progress``, ``failed``). ``SET search_path`` to a schema that
does not exist succeeds silently, so a walker that visits one fails on its first
query. Here a database is taken through the real boot path, which provisions
one ready practice, and is then given one practice row of each other status
with no schema behind it. Every walker must run to completion over that:

* the two shared listings return the ready practice and none of the others;
* ``bin/migrate_tenants.py``, run as the deploy runs it, migrates the ready
  practice and exits 0;
* the audit-retention and hard-purge jobs finish with exit 0.

The other walkers (payer enrollment refresh, intake adoption, the telehealth
webhook, the claims fan-out) list practices only through
``list_active_practice_registry``, so the listing test is theirs too.

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
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text

from . import scratch_db

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_BACKEND_DIR = Path(__file__).resolve().parents[2]

#: Every status whose practice has no complete schema, each given a row and
#: no schema at all.
_NOT_READY = ("pending", "archived", "in_progress", "failed")


def _register_without_schema(engine: Engine, provisioning_status: str) -> str:
    schema = f"practice_noschema_{provisioning_status}"
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.practices"
                " (id, name, schema_name, owner_email, product, status, is_active,"
                "  created_at, is_pentest, provisioning_status)"
                " VALUES (:id, 'A practice', :schema, '', 'pablo', 'active', TRUE,"
                "         :ts, FALSE, :ps)"
            ),
            {
                "id": schema,
                "schema": schema,
                "ts": datetime.now(UTC),
                "ps": provisioning_status,
            },
        )
    return schema


@pytest.fixture(scope="module")
def scratch_url() -> Iterator[str]:
    """A booted database with one ready practice and four with no schema."""
    from app.db.platform_bootstrap import bring_platform_to_head  # noqa: PLC0415
    from app.db.provisioning import ensure_schemas  # noqa: PLC0415

    scratch = scratch_db.scratch_name("pablo_noschema")
    admin = create_engine(_DB_URL, isolation_level="AUTOCOMMIT", pool_pre_ping=True)
    scratch_db.create(admin, scratch)
    url = scratch_db.swap_database(_DB_URL, scratch)
    eng = create_engine(url, pool_pre_ping=True)
    try:
        bring_platform_to_head(eng, str(_BACKEND_DIR / "alembic.ini"))
        ensure_schemas(eng)
        for provisioning_status in _NOT_READY:
            _register_without_schema(eng, provisioning_status)
        eng.dispose()
        yield url
    finally:
        eng.dispose()
        scratch_db.drop(admin, scratch)
        admin.dispose()


@pytest.fixture
def engine(scratch_url: str) -> Iterator[Engine]:
    eng = create_engine(scratch_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _ready_schemas(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return list(
            conn.execute(
                text(
                    "SELECT schema_name FROM platform.practices"
                    " WHERE provisioning_status = 'ready' AND schema_name <> 'practice'"
                    " ORDER BY schema_name"
                )
            ).scalars()
        )


def test_the_listings_return_the_ready_practice_and_none_without_a_schema(
    engine: Engine,
) -> None:
    from app.db.migrate_tenants import (  # noqa: PLC0415
        list_active_practice_registry,
        list_active_tenant_schemas,
    )

    ready = _ready_schemas(engine)
    assert len(ready) == 1

    tenant_schemas = list_active_tenant_schemas(engine)
    registry_schemas = [schema for schema, _id in list_active_practice_registry(engine)]

    assert tenant_schemas == ready
    assert ready[0] in registry_schemas
    for provisioning_status in _NOT_READY:
        missing = f"practice_noschema_{provisioning_status}"
        assert missing not in tenant_schemas
        assert missing not in registry_schemas


def test_migrate_tenants_migrates_the_ready_practice_and_exits_zero(
    scratch_url: str, engine: Engine
) -> None:
    env = {**os.environ, "DATABASE_URL": scratch_url, "DATABASE_BACKEND": "postgres"}
    result = subprocess.run(  # noqa: S603 (trusted: this interpreter, test-controlled args)
        [sys.executable, str(_BACKEND_DIR / "bin" / "migrate_tenants.py")],
        cwd=_BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "found 1 active tenant schema(s) to migrate" in output
    (ready,) = _ready_schemas(engine)
    assert ready in output
    assert "practice_noschema_" not in output


def test_audit_retention_runs_to_completion(engine: Engine) -> None:
    from app.jobs import audit_retention_cron  # noqa: PLC0415

    with patch.object(audit_retention_cron, "get_engine", return_value=engine):
        assert audit_retention_cron.run([]) == 0


def test_hard_purge_runs_to_completion(engine: Engine) -> None:
    from app.jobs import hard_purge_cron  # noqa: PLC0415

    with patch.object(hard_purge_cron, "get_engine", return_value=engine):
        assert hard_purge_cron.run([]) == 0
