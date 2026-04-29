# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: create_practice_schema stamps alembic_version at head.

Without this stamp, the per-tenant fan-out tool (pa-5in.1) has no version to
upgrade FROM and any new migration would either no-op or error on the tenant.

Requires real Postgres — see test_tenant_isolation.py for the same pattern.
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db import DEFAULT_PRACTICE_SCHEMA
from app.db.provisioning import _ALEMBIC_INI_PATH, create_practice_schema
from sqlalchemy import create_engine, text

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres. "
        "Start proxy with: make db-dev-proxy"
    ),
)


@pytest.fixture
def engine():
    return create_engine(_db_url, pool_pre_ping=True)


@pytest.fixture
def tenant_schema(engine):
    schema = f"practice_test_stamp_{uuid.uuid4().hex[:8]}"
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.commit()


def _alembic_head() -> str:
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))
    head = script.get_current_head()
    assert head is not None, "alembic has no head — check migrations/ exists"
    return head


def test_create_practice_schema_stamps_alembic_version_at_head(
    engine, tenant_schema: str
) -> None:
    create_practice_schema(engine, tenant_schema)

    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT version_num FROM {tenant_schema}.alembic_version")  # noqa: S608
        ).fetchone()

    assert row is not None, f"alembic_version row missing on {tenant_schema}"
    assert row[0] == _alembic_head()


def test_create_practice_schema_stamp_is_idempotent(engine, tenant_schema: str) -> None:
    create_practice_schema(engine, tenant_schema)
    create_practice_schema(engine, tenant_schema)

    with engine.connect() as conn:
        rows = conn.execute(
            text(f"SELECT version_num FROM {tenant_schema}.alembic_version")  # noqa: S608
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == _alembic_head()


def _alembic_version_state(conn, schema: str) -> str | None:
    exists = conn.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = :s AND table_name = 'alembic_version'"
        ),
        {"s": schema},
    ).fetchone()
    if not exists:
        return None
    return conn.execute(
        text(f"SELECT version_num FROM {schema}.alembic_version")  # noqa: S608
    ).scalar()


def test_template_schema_is_not_stamped_by_provisioning(engine) -> None:
    """The 'practice' template's alembic_version is owned by deploy-time
    `alembic upgrade head`, not by create_practice_schema. Calling
    create_practice_schema on the template must not create or modify the
    alembic_version row.
    """
    with engine.connect() as conn:
        before = _alembic_version_state(conn, DEFAULT_PRACTICE_SCHEMA)

    create_practice_schema(engine, DEFAULT_PRACTICE_SCHEMA)

    with engine.connect() as conn:
        after = _alembic_version_state(conn, DEFAULT_PRACTICE_SCHEMA)

    assert before == after
