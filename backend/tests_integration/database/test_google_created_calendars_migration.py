# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: the record of calendars Pablo created, and its backfill.

``c7d2a9e4f1b6`` adds ``google_created_calendars`` and records every calendar
already known to be Pablo's: the remembered ``app_calendar_id`` and the write
calendar of a connection that writes to a calendar Pablo made. A connection
writing to the main calendar records nothing for it. Both source tables are
FORCE-RLS'd and this suite's role is NOBYPASSRLS, so this also proves the
backfill sees the rows.
"""

from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import TenantStatus, _alembic_config_for, upgrade_tenant_schema
from app.db.provisioning import create_practice_schema, ensure_schemas
from sqlalchemy import create_engine, text

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_PARENT = "b8e3f1c6d2a5"
_UNDER_TEST = "c7d2a9e4f1b6"
_REMEMBERED = "11111111-2222-3333-4444-555555555555"
_WRITING_TO_PABLOS = "66666666-7777-8888-9999-000000000000"
_WRITING_TO_MAIN = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.fixture
def engine():
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    return eng


def _set_rls(conn, schema: str, table: str, *, on: bool) -> None:
    if on:
        conn.execute(text(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY"))
    else:
        conn.execute(text(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY"))


def _downgrade(engine, schema: str, target: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, target)


@pytest.fixture
def tenant_at_parent(engine):
    schema = f"practice_test_created_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    _downgrade(engine, schema, _PARENT)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "google_calendar_settings", on=False)
        _set_rls(conn, schema, "google_calendar_tokens", on=False)
        conn.execute(
            text(
                "INSERT INTO google_calendar_settings (user_id, app_calendar_id) "
                "VALUES (:a, 'remembered@group.calendar.google.test')"
            ),
            {"a": _REMEMBERED},
        )
        conn.execute(
            text(
                "INSERT INTO google_calendar_tokens "
                "(user_id, encrypted_tokens, write_target, calendar_id) VALUES "
                "(:b, 'x', 'app_calendar', 'writing@group.calendar.google.test'), "
                "(:c, 'x', 'primary', 'clinician@example.test')"
            ),
            {"b": _WRITING_TO_PABLOS, "c": _WRITING_TO_MAIN},
        )
        _set_rls(conn, schema, "google_calendar_settings", on=True)
        _set_rls(conn, schema, "google_calendar_tokens", on=True)

    yield schema

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def _recorded(engine, schema: str) -> dict[str, tuple[str, bool]]:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "google_created_calendars", on=False)
        try:
            rows = conn.execute(
                text(
                    "SELECT user_id::text, calendar_id, marked_at IS NOT NULL "
                    "FROM google_created_calendars"
                )
            ).all()
        finally:
            _set_rls(conn, schema, "google_created_calendars", on=True)
    return {row[0]: (row[1], row[2]) for row in rows}


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    script = ScriptDirectory.from_config(_alembic_config_for("practice"))
    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_calendars_already_known_to_be_pablos_are_recorded_unmarked(
    engine, tenant_at_parent
) -> None:
    schema = tenant_at_parent

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    assert _recorded(engine, schema) == {
        _REMEMBERED: ("remembered@group.calendar.google.test", False),
        _WRITING_TO_PABLOS: ("writing@group.calendar.google.test", False),
    }


def test_running_it_again_records_nothing_twice(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    upgrade_tenant_schema(engine, schema)
    _downgrade(engine, schema, _PARENT)

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    assert len(_recorded(engine, schema)) == 2
