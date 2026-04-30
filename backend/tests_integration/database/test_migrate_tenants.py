# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: tenant fan-out brings every schema to head.

Provisions 2-3 fake practice schemas at varying alembic states (at head,
older revision, missing alembic_version entirely) and asserts the fan-out
emits the expected per-tenant statuses + leaves every schema at HEAD.

Requires real Postgres — same gate as ``test_provisioning_stamp.py``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import (
    TenantStatus,
    fan_out,
    list_active_tenant_schemas,
    upgrade_tenant_schema,
)
from app.db.provisioning import (
    _ALEMBIC_INI_PATH,
    create_practice_schema,
    ensure_schemas,
)
from sqlalchemy import create_engine, text

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres."
    ),
)


def _alembic_head() -> str:
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))
    head = script.get_current_head()
    assert head is not None
    return head


@pytest.fixture
def engine():
    return create_engine(_db_url, pool_pre_ping=True)


@pytest.fixture
def tenant_factory(engine):
    """Yields a callable that creates a uniquely-named practice schema and
    registers it in ``platform.practices``. Drops everything on teardown.
    """
    ensure_schemas(engine)
    created: list[str] = []
    suffix = uuid.uuid4().hex[:8]

    def _make(label: str, *, register: bool = True, stamp: bool = True) -> str:
        schema = f"practice_test_fan_{label}_{suffix}"
        create_practice_schema(engine, schema)
        if not stamp:
            with engine.begin() as conn:
                conn.execute(text(f"DROP TABLE IF EXISTS {schema}.alembic_version"))
        if register:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        f"INSERT INTO {PLATFORM_SCHEMA}.practices"  # noqa: S608
                        " (id, name, schema_name, owner_email, owner_user_id,"
                        "  product, status, is_active, created_at, is_pentest)"
                        " VALUES (:id, :name, :schema, '', '', 'pablo',"
                        "         'active', TRUE, :ts, FALSE)"
                    ),
                    {
                        "id": schema,
                        "name": f"Test {label}",
                        "schema": schema,
                        "ts": datetime.now(UTC),
                    },
                )
        created.append(schema)
        return schema

    yield _make

    with engine.begin() as conn:
        for schema in created:
            conn.execute(
                text(
                    f"DELETE FROM {PLATFORM_SCHEMA}.practices"  # noqa: S608
                    " WHERE schema_name = :s"
                ),
                {"s": schema},
            )
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def _version_in(engine, schema: str) -> str | None:
    with engine.connect() as conn:
        return conn.execute(
            text(f"SELECT version_num FROM {schema}.alembic_version")  # noqa: S608
        ).scalar()


def test_list_active_tenant_schemas_excludes_template(engine, tenant_factory) -> None:
    schema = tenant_factory("listing")
    schemas = list_active_tenant_schemas(engine)
    assert "practice" not in schemas
    assert schema in schemas


def test_upgrade_tenant_at_head_reports_already_at_head(engine, tenant_factory) -> None:
    schema = tenant_factory("athead")
    result = upgrade_tenant_schema(engine, schema)
    assert result.status is TenantStatus.ALREADY_AT_HEAD
    assert _version_in(engine, schema) == _alembic_head()


def test_upgrade_tenant_missing_version_auto_stamps(engine, tenant_factory) -> None:
    """Tenants provisioned before pa-5in.2 lack alembic_version. The fan-out
    auto-stamps them at HEAD with a clear log line — pa-5in epic note documents
    this choice. After stamping the row is at HEAD and re-running is a no-op.
    """
    schema = tenant_factory("legacy", stamp=False)
    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.STAMPED
    assert _version_in(engine, schema) == _alembic_head()

    again = upgrade_tenant_schema(engine, schema)
    assert again.status is TenantStatus.ALREADY_AT_HEAD


def test_fan_out_continues_past_one_failure(engine, tenant_factory) -> None:
    good = tenant_factory("good_a")
    bogus = "practice_test_does_not_exist_zzz"
    other = tenant_factory("good_b")

    results = fan_out(engine, [good, bogus, other])
    by_schema = {r.schema: r for r in results}

    assert by_schema[good].ok
    assert by_schema[other].ok
    assert by_schema[bogus].status is TenantStatus.FAILED
    assert _version_in(engine, good) == _alembic_head()
    assert _version_in(engine, other) == _alembic_head()


def test_fan_out_is_idempotent(engine, tenant_factory) -> None:
    schemas = [tenant_factory("idem_a"), tenant_factory("idem_b")]

    first = fan_out(engine, schemas)
    second = fan_out(engine, schemas)

    assert all(r.status is TenantStatus.ALREADY_AT_HEAD for r in second), first
