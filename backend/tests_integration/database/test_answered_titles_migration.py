# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: the answered title and the feed's title style land in a tenant.

``c3f7a1d9e2b4`` adds ``patient_source_mappings.answered_title`` and
``ical_sync_configs.title_style``, both nullable. This builds a tenant at the
parent revision holding a remembered answer, runs the real migration, writes
and reads the new columns through the repositories the app uses, and checks
the downgrade takes the columns away and nothing else.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import TenantStatus, _alembic_config_for, upgrade_tenant_schema
from app.db.provisioning import _ALEMBIC_INI_PATH, create_practice_schema, ensure_schemas
from app.patients.identifiers import calendar_scope, identifier_digest
from app.repositories.ical_sync_config import ICalSyncConfig
from app.repositories.patient_source_mapping import PatientSourceMapping
from app.repositories.postgres.ical_sync_config import PostgresICalSyncConfigRepository
from app.repositories.postgres.patient_source_mapping import (
    PostgresPatientSourceMappingRepository,
)
from app.settings import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_PARENT = "b8e1f5a3c7d2"
_UNDER_TEST = "c3f7a1d9e2b4"
_USER_ID = "11111111-2222-3333-4444-555555555555"
#: The calendar the repository test's answer is remembered under.
_CALENDAR = calendar_scope("me@example.test")


@pytest.fixture
def engine():
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    return eng


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch):
    """The secret identifiers are digested under; the repository test needs it."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _set_rls(conn, schema: str, table: str, *, on: bool) -> None:
    if on:
        conn.execute(text(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY"))
    else:
        conn.execute(text(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY"))


def _rows(engine, schema: str, table: str, sql: str):
    """Read past the row policy — the suite's role is NOBYPASSRLS."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, table, on=False)
        try:
            return conn.execute(text(sql)).all()
        finally:
            _set_rls(conn, schema, table, on=True)


def _columns(engine, schema: str, table: str) -> set[str]:
    with engine.connect() as conn:
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


@pytest.fixture
def tenant_at_parent(engine):
    """A tenant at ``_PARENT`` holding one remembered answer and one feed."""
    schema = f"practice_test_titles_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "patient_source_mappings", on=False)
        _set_rls(conn, schema, "ical_sync_configs", on=False)
        conn.execute(
            text(
                "INSERT INTO patient_source_mappings "
                "(doc_id, user_id, source, source_identifier, answer, patient_id, created_at) "
                "VALUES (:doc, :user, 'google_calendar', 'series:wk', 'not_a_client', NULL, :now)"
            ),
            {
                "doc": f"{_USER_ID}_google_calendar_series:wk",
                "user": _USER_ID,
                "now": datetime.now(UTC),
            },
        )
        conn.execute(
            text(
                "INSERT INTO ical_sync_configs "
                "(doc_id, user_id, ehr_system, encrypted_feed_url, connected_at) "
                "VALUES (:doc, :user, 'simplepractice', 'enc', :now)"
            ),
            {"doc": f"{_USER_ID}_simplepractice", "user": _USER_ID, "now": datetime.now(UTC)},
        )
        _set_rls(conn, schema, "patient_source_mappings", on=True)
        _set_rls(conn, schema, "ical_sync_configs", on=True)

    yield schema

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    script = ScriptDirectory.from_config(_alembic_config_for("practice"))
    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT
    assert _ALEMBIC_INI_PATH.exists()


def test_the_columns_arrive_and_the_rows_stay(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    assert "answered_title" not in _columns(engine, schema, "patient_source_mappings")
    assert "title_style" not in _columns(engine, schema, "ical_sync_configs")

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    assert "answered_title" in _columns(engine, schema, "patient_source_mappings")
    assert "title_style" in _columns(engine, schema, "ical_sync_configs")
    [(identifier, answer, title)] = _rows(
        engine,
        schema,
        "patient_source_mappings",
        "SELECT source_identifier, answer, answered_title FROM patient_source_mappings",
    )
    assert (identifier, answer, title) == ("series:wk", "not_a_client", None)
    [(style,)] = _rows(
        engine, schema, "ical_sync_configs", "SELECT title_style FROM ical_sync_configs"
    )
    assert style is None


def test_the_repositories_write_and_read_the_new_columns(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    upgrade_tenant_schema(engine, schema)

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        session.execute(text(f"SET app.current_user_id = '{_USER_ID}'"))
        mappings = PostgresPatientSourceMappingRepository(session)
        mappings.save(
            PatientSourceMapping(
                scope=_CALENDAR,
                source="google_calendar",
                identifier_digest=identifier_digest("series:wk"),
                patient_id=None,
                answered_by_user_id=_USER_ID,
                answer="not_a_client",
                answered_title="a" * 64,
            )
        )
        configs = PostgresICalSyncConfigRepository(session)
        configs.update_sync_status(_USER_ID, "simplepractice", error=None, title_style="initials")
        session.commit()

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        session.execute(text(f"SET app.current_user_id = '{_USER_ID}'"))
        [mapping] = PostgresPatientSourceMappingRepository(session).list_by_source(
            _CALENDAR, "google_calendar"
        )
        assert mapping.identifier_digest == identifier_digest("series:wk")
        assert mapping.answered_title == "a" * 64
        config = PostgresICalSyncConfigRepository(session).get(_USER_ID, "simplepractice")
        assert config is not None
        assert config.title_style == "initials"
        # An errored read keeps the style on record.
        PostgresICalSyncConfigRepository(session).update_sync_status(
            _USER_ID, "simplepractice", error="boom"
        )
        session.commit()

    with Session(engine) as session:
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        session.execute(text(f"SET app.current_user_id = '{_USER_ID}'"))
        config = PostgresICalSyncConfigRepository(session).get(_USER_ID, "simplepractice")
        assert config is not None
        assert (config.title_style, config.last_sync_error) == ("initials", "boom")
        assert isinstance(ICalSyncConfig.from_dict(config.to_dict()), ICalSyncConfig)


def test_the_downgrade_takes_the_columns_and_nothing_else(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    upgrade_tenant_schema(engine, schema)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    assert "answered_title" not in _columns(engine, schema, "patient_source_mappings")
    assert "title_style" not in _columns(engine, schema, "ical_sync_configs")
    [(identifier,)] = _rows(
        engine,
        schema,
        "patient_source_mappings",
        "SELECT source_identifier FROM patient_source_mappings",
    )
    assert identifier == "series:wk"
    [(ehr,)] = _rows(
        engine, schema, "ical_sync_configs", "SELECT ehr_system FROM ical_sync_configs"
    )
    assert ehr == "simplepractice"
