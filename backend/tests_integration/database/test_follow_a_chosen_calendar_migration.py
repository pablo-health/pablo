# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: following becomes a chosen calendar, additively.

``d6a2e9f4b1c8`` adds ``google_calendar_settings.follow_calendar_id``,
``external_calendar_events.calendar_id`` and ``appointments.outside_calendar_id``,
and maps every clinician following the main calendar to ``'primary'``. The old
``follow_main_calendar`` column stays, so an image from before the revision
keeps working mid-deploy. The settings table is FORCE-RLS'd and this suite's
role is NOBYPASSRLS, so this also proves the backfill sees the rows.
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
from app.repositories.postgres.google_calendar_token import (
    PostgresGoogleCalendarTokenRepository,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_PARENT = "c3f7a1d9e2b4"
_UNDER_TEST = "d6a2e9f4b1c8"
_FOLLOWING = "11111111-2222-3333-4444-555555555555"
_NOT_FOLLOWING = "66666666-7777-8888-9999-000000000000"


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


def _settings(engine, schema: str) -> dict[str, tuple]:
    """Every settings row, read past the row policy."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "google_calendar_settings", on=False)
        try:
            columns = _columns(conn, schema, "google_calendar_settings")
            sql = (
                "SELECT user_id::text, follow_main_calendar, follow_calendar_id "
                "FROM google_calendar_settings"
                if "follow_calendar_id" in columns
                else "SELECT user_id::text, follow_main_calendar FROM google_calendar_settings"
            )
            rows = conn.execute(text(sql)).all()
            return {row[0]: tuple(row[1:]) for row in rows}
        finally:
            _set_rls(conn, schema, "google_calendar_settings", on=True)


def _columns(conn, schema: str, table: str) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": schema, "table": table},
        )
    }


def _has(engine, schema: str, table: str, column: str) -> bool:
    with engine.connect() as conn:
        return column in _columns(conn, schema, table)


def _downgrade(engine, schema: str, target: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, target)


@pytest.fixture
def tenant_at_parent(engine):
    """A tenant at ``_PARENT``: one clinician following the main calendar, one not."""
    schema = f"practice_test_follow_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    _downgrade(engine, schema, _PARENT)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "google_calendar_settings", on=False)
        conn.execute(
            text(
                "INSERT INTO google_calendar_settings (user_id, follow_main_calendar) "
                "VALUES (:a, true), (:b, false)"
            ),
            {"a": _FOLLOWING, "b": _NOT_FOLLOWING},
        )
        _set_rls(conn, schema, "google_calendar_settings", on=True)

    yield schema

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    script = ScriptDirectory.from_config(_alembic_config_for("practice"))
    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_following_the_main_calendar_becomes_primary(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    assert _settings(engine, schema) == {
        _FOLLOWING: (True, "primary"),
        _NOT_FOLLOWING: (False, None),
    }


def test_the_revision_only_adds(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent

    upgrade_tenant_schema(engine, schema)

    assert _has(engine, schema, "google_calendar_settings", "follow_main_calendar")
    assert _has(engine, schema, "google_calendar_settings", "follow_calendar_id")
    assert _has(engine, schema, "external_calendar_events", "calendar_id")
    assert _has(engine, schema, "appointments", "outside_calendar_id")


def test_choosing_a_calendar_keeps_the_old_flag_in_step(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    upgrade_tenant_schema(engine, schema)

    choices = (
        (_FOLLOWING, "team@group.test", False),
        (_NOT_FOLLOWING, "me@example.test", True),
    )
    for user_id, calendar_id, main in choices:
        with Session(engine) as session:
            session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            session.execute(text(f"SET app.current_user_id = '{user_id}'"))
            PostgresGoogleCalendarTokenRepository(session).set_followed_calendar(
                user_id, calendar_id, main_calendar=main
            )
            session.commit()

    # An older image follows only the main calendar: it is told "on" only
    # for a clinician following that one, never for another calendar.
    assert _settings(engine, schema) == {
        _FOLLOWING: (False, "team@group.test"),
        _NOT_FOLLOWING: (True, "me@example.test"),
    }


def test_primary_is_resolved_only_while_it_is_still_stored(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    upgrade_tenant_schema(engine, schema)

    results = []
    for user_id in (_FOLLOWING, _NOT_FOLLOWING):
        with Session(engine) as session:
            session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            session.execute(text(f"SET app.current_user_id = '{user_id}'"))
            results.append(
                PostgresGoogleCalendarTokenRepository(session).resolve_followed_main_calendar(
                    user_id, "me@example.test"
                )
            )
            session.commit()

    assert results == [True, False]
    assert _settings(engine, schema) == {
        _FOLLOWING: (True, "me@example.test"),
        _NOT_FOLLOWING: (False, None),
    }


def test_a_rerun_changes_nothing(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    upgrade_tenant_schema(engine, schema)
    before = _settings(engine, schema)

    _downgrade(engine, schema, _PARENT)
    # The old flag was kept, so a second upgrade maps it again the same way.
    upgrade_tenant_schema(engine, schema)

    assert _settings(engine, schema) == before


def test_the_downgrade_takes_the_new_columns_and_nothing_else(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    upgrade_tenant_schema(engine, schema)

    _downgrade(engine, schema, _PARENT)

    assert not _has(engine, schema, "google_calendar_settings", "follow_calendar_id")
    assert not _has(engine, schema, "external_calendar_events", "calendar_id")
    assert not _has(engine, schema, "appointments", "outside_calendar_id")
    assert _settings(engine, schema) == {_FOLLOWING: (True,), _NOT_FOLLOWING: (False,)}
