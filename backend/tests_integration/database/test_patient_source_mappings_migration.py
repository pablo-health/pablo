# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: ``ical_client_mappings`` becomes ``patient_source_mappings``.

``f4b2d8a61c73`` renames the table and its two iCal-specific columns in
place. The rows are clinicians' own answers to "which patient is J.A.?", so
losing one means a feed stops matching a client nobody is asked about again.
This builds a tenant at the parent revision holding a mapping, runs the real
migration, and reads the row back under the new names. The same revision adds
the "not a client" answer, and its check constraints are exercised here too.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import TenantStatus, _alembic_config_for, upgrade_tenant_schema
from app.db.provisioning import _ALEMBIC_INI_PATH, create_practice_schema, ensure_schemas
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

#: The revision immediately before the one under test.
_PARENT = "c8a2e5f19d34"
_UNDER_TEST = "f4b2d8a61c73"

_USER_ID = "11111111-2222-3333-4444-555555555555"


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


def _read(engine, schema: str, table: str, sql: str):
    """Read past the row policy — the suite's role is NOBYPASSRLS."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, table, on=False)
        try:
            return conn.execute(text(sql)).all()
        finally:
            _set_rls(conn, schema, table, on=True)


@pytest.fixture
def tenant_with_a_mapping(engine):
    """A tenant at ``_PARENT`` holding one iCal mapping, RLS forced."""
    schema = f"practice_test_srcmap_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    patient_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        for table in ("patients", "ical_client_mappings"):
            _set_rls(conn, schema, table, on=False)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower,"
                " last_name_lower, status, session_count, created_at, updated_at)"
                " VALUES (:id, 'Jane', 'Adams', 'jane', 'adams', 'active', 0, :ts, :ts)"
            ),
            {"id": patient_id, "ts": now},
        )
        conn.execute(
            text(
                "INSERT INTO ical_client_mappings (doc_id, user_id, ehr_system,"
                " client_identifier, patient_id, created_at)"
                " VALUES (:doc, :uid, 'simplepractice', 'J.A.', :pid, :ts)"
            ),
            {
                "doc": f"{_USER_ID}_simplepractice_J.A.",
                "uid": _USER_ID,
                "pid": patient_id,
                "ts": now,
            },
        )
        for table in ("patients", "ical_client_mappings"):
            _set_rls(conn, schema, table, on=True)

    yield schema, patient_id

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    """If the chain is re-pointed, the fixture must walk back to the new parent."""
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))

    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_the_rename_keeps_every_row(engine, tenant_with_a_mapping) -> None:
    schema, patient_id = tenant_with_a_mapping

    result = upgrade_tenant_schema(engine, schema)
    assert result.status is TenantStatus.SUCCESS, result.detail

    rows = _read(
        engine,
        schema,
        "patient_source_mappings",
        "SELECT doc_id, user_id::text, source, source_identifier, patient_id::text"
        " FROM patient_source_mappings",
    )
    assert [tuple(r) for r in rows] == [
        (f"{_USER_ID}_simplepractice_J.A.", _USER_ID, "simplepractice", "J.A.", patient_id)
    ]


def test_the_old_table_is_gone_and_its_keys_are_renamed(engine, tenant_with_a_mapping) -> None:
    schema, _ = tenant_with_a_mapping

    upgrade_tenant_schema(engine, schema)

    with engine.connect() as conn:
        old = conn.execute(text("SELECT to_regclass(:t)"), {"t": f"{schema}.ical_client_mappings"})
        assert old.scalar() is None
        names = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = to_regclass(:t)"
                    " UNION SELECT indexname FROM pg_indexes"
                    " WHERE schemaname = :s AND tablename = 'patient_source_mappings'"
                ),
                {"t": f"{schema}.patient_source_mappings", "s": schema},
            )
        }
    assert {
        "patient_source_mappings_pkey",
        "patient_source_mappings_patient_id_fkey",
        "ix_patient_source_mappings_user_id",
    } <= names
    assert not any(name.startswith("ical_client_mappings") for name in names)


def test_the_renamed_table_is_still_row_scoped(engine, tenant_with_a_mapping) -> None:
    """A rename must not leave the table readable without a policy match."""
    schema, _ = tenant_with_a_mapping

    upgrade_tenant_schema(engine, schema)

    with engine.connect() as conn:
        forced = conn.execute(
            text(
                "SELECT c.relrowsecurity AND c.relforcerowsecurity FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relname = 'patient_source_mappings'"
            ),
            {"s": schema},
        ).scalar()
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        visible = conn.execute(text("SELECT count(*) FROM patient_source_mappings")).scalar()
    assert forced is True
    assert visible == 0, "without the policy GUC the row must not be visible"


def test_the_downgrade_restores_the_old_names_with_the_row(engine, tenant_with_a_mapping) -> None:
    schema, patient_id = tenant_with_a_mapping
    upgrade_tenant_schema(engine, schema)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    rows = _read(
        engine,
        schema,
        "ical_client_mappings",
        "SELECT ehr_system, client_identifier, patient_id::text FROM ical_client_mappings",
    )
    assert [tuple(r) for r in rows] == [("simplepractice", "J.A.", patient_id)]


def test_existing_rows_become_client_answers(engine, tenant_with_a_mapping) -> None:
    schema, _ = tenant_with_a_mapping

    upgrade_tenant_schema(engine, schema)

    rows = _read(
        engine, schema, "patient_source_mappings", "SELECT answer FROM patient_source_mappings"
    )
    assert [r[0] for r in rows] == ["client"]


def _insert(engine, schema: str, identifier: str, answer: str, patient_id: str | None) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        # Row security is stood down for the seed only. A rejected insert
        # rolls the whole transaction back, the flags with it.
        _set_rls(conn, schema, "patient_source_mappings", on=False)
        conn.execute(
            text(
                "INSERT INTO patient_source_mappings (doc_id, user_id, source,"
                " source_identifier, answer, patient_id, created_at)"
                " VALUES (:doc, :uid, 'google_calendar', :ident, :answer, :pid, now())"
            ),
            {
                "doc": f"{_USER_ID}_google_calendar_{identifier}",
                "uid": _USER_ID,
                "ident": identifier,
                "answer": answer,
                "pid": patient_id,
            },
        )
        _set_rls(conn, schema, "patient_source_mappings", on=True)


def test_a_not_a_client_answer_needs_no_patient(engine, tenant_with_a_mapping) -> None:
    schema, _ = tenant_with_a_mapping
    upgrade_tenant_schema(engine, schema)

    _insert(engine, schema, "series:standup", "not_a_client", None)

    rows = _read(
        engine,
        schema,
        "patient_source_mappings",
        "SELECT patient_id FROM patient_source_mappings WHERE answer = 'not_a_client'",
    )
    assert [r[0] for r in rows] == [None]


@pytest.mark.parametrize(
    ("answer", "with_patient"),
    [("client", False), ("not_a_client", True), ("maybe", False)],
    ids=["client-without-patient", "not-a-client-with-patient", "unknown-answer"],
)
def test_the_check_constraints_reject_an_inconsistent_answer(
    engine, tenant_with_a_mapping, answer: str, with_patient: bool
) -> None:
    schema, patient_id = tenant_with_a_mapping
    upgrade_tenant_schema(engine, schema)

    with pytest.raises(IntegrityError, match="ck_patient_source_mappings_"):
        _insert(engine, schema, "series:bad", answer, patient_id if with_patient else None)


def test_the_downgrade_drops_not_a_client_answers_it_cannot_hold(
    engine, tenant_with_a_mapping
) -> None:
    """The old table has no way to say "not a client", so those rows go."""
    schema, patient_id = tenant_with_a_mapping
    upgrade_tenant_schema(engine, schema)
    _insert(engine, schema, "series:standup", "not_a_client", None)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    rows = _read(
        engine,
        schema,
        "ical_client_mappings",
        "SELECT client_identifier, patient_id::text FROM ical_client_mappings",
    )
    assert [tuple(r) for r in rows] == [("J.A.", patient_id)]


def _visible_not_a_client_rows(engine, schema: str, user_id: str) -> int:
    """Count through the row policy, as the given clinician."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": user_id})
        return conn.execute(
            text("SELECT count(*) FROM patient_source_mappings WHERE answer = 'not_a_client'")
        ).scalar_one()


def test_a_not_a_client_row_is_visible_to_its_clinician_and_nobody_else(
    engine, tenant_with_a_mapping
) -> None:
    """A row with no patient must not fall through the policy either way.

    The table is owned per clinician (``user_id``), so a not-a-client answer
    is its author's alone: a patient-access policy would hide it from them,
    and no policy at all would show it to everyone.
    """
    schema, _ = tenant_with_a_mapping
    upgrade_tenant_schema(engine, schema)
    _insert(engine, schema, "series:standup", "not_a_client", None)

    with engine.connect() as conn:
        policies = {
            row[0]
            for row in conn.execute(
                text(
                    "SELECT policyname FROM pg_policies"
                    " WHERE schemaname = :s AND tablename = 'patient_source_mappings'"
                ),
                {"s": schema},
            )
        }
    assert "rls_user_isolation" in policies

    assert _visible_not_a_client_rows(engine, schema, _USER_ID) == 1
    assert _visible_not_a_client_rows(engine, schema, str(uuid.uuid4())) == 0


def test_a_provider_id_longer_than_the_old_column_fits(engine, tenant_with_a_mapping) -> None:
    """Calendar providers' series ids run to 1024 characters."""
    schema, _ = tenant_with_a_mapping
    upgrade_tenant_schema(engine, schema)
    identifier = "series:" + "x" * 1024

    _insert(engine, schema, identifier, "not_a_client", None)

    rows = _read(
        engine,
        schema,
        "patient_source_mappings",
        "SELECT length(source_identifier) FROM patient_source_mappings"
        " WHERE answer = 'not_a_client'",
    )
    assert [r[0] for r in rows] == [len(identifier)]
