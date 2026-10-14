# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: imported notes awaiting review become reference copies.

``d3b81f6c5a27`` moves an imported session still awaiting review to the
``imported`` status, so it stops being offered for signing. One whose note was
already signed here keeps its status: the revision must not undo a signature.
This builds a tenant at the parent revision holding each kind of session, runs
the real migration, and reads the statuses back.
"""

from __future__ import annotations

import json
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

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

#: The revision immediately before the one under test.
_PARENT = "a7e3c5d19b42"
_UNDER_TEST = "d3b81f6c5a27"

_USER_ID = "11111111-2222-3333-4444-555555555555"
_TABLES = ("patients", "therapy_sessions", "notes")


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


def _session(conn, patient_id: str, *, source: str | None, status: str, signed: bool) -> str:
    """A session with its note, signed here or not."""
    now = datetime.now(UTC)
    session_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date,"
            " session_number, status, transcript, created_at, source)"
            " VALUES (:id, :uid, :pid, :ts, 1, :status, CAST(:transcript AS jsonb), :ts, :source)"
        ),
        {
            "id": session_id,
            "uid": _USER_ID,
            "pid": patient_id,
            "ts": now,
            "status": status,
            "transcript": json.dumps({"format": "txt", "content": "Chief complaint: sleep."}),
            "source": source,
        },
    )
    conn.execute(
        text(
            "INSERT INTO notes (id, patient_id, session_id, note_type, status, finalized_at,"
            " created_at, updated_at)"
            " VALUES (:id, :pid, :sid, 'soap', 'complete', :finalized, :ts, :ts)"
        ),
        {
            "id": str(uuid.uuid4()),
            "pid": patient_id,
            "sid": session_id,
            "finalized": now if signed else None,
            "ts": now,
        },
    )
    return session_id


@pytest.fixture
def tenant_with_imports(engine):
    """A tenant at ``_PARENT`` holding imported and recorded sessions, RLS forced."""
    schema = f"practice_test_imported_{uuid.uuid4().hex[:8]}"
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
        for table in _TABLES:
            _set_rls(conn, schema, table, on=False)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower,"
                " last_name_lower, status, session_count, created_at, updated_at)"
                " VALUES (:id, 'Jane', 'Adams', 'jane', 'adams', 'active', 0, :ts, :ts)"
            ),
            {"id": patient_id, "ts": now},
        )
        sessions = {
            "awaiting": _session(
                conn, patient_id, source="imported", status="pending_review", signed=False
            ),
            # Signed from the note page, which leaves the session's status alone.
            "signed_awaiting": _session(
                conn, patient_id, source="imported", status="pending_review", signed=True
            ),
            "signed": _session(
                conn, patient_id, source="imported", status="finalized", signed=True
            ),
            "recorded": _session(
                conn, patient_id, source="companion", status="pending_review", signed=False
            ),
        }
        for table in _TABLES:
            _set_rls(conn, schema, table, on=True)

    yield schema, sessions

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def _statuses(engine, schema: str) -> dict[str, str]:
    """Read past the row policy — the suite's role is NOBYPASSRLS."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "therapy_sessions", on=False)
        try:
            rows = conn.execute(text("SELECT id::text, status FROM therapy_sessions")).all()
        finally:
            _set_rls(conn, schema, "therapy_sessions", on=True)
    return {row[0]: row[1] for row in rows}


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    """If the chain is re-pointed, the fixture must walk back to the new parent."""
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))

    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_only_unsigned_imports_awaiting_review_move(engine, tenant_with_imports) -> None:
    schema, sessions = tenant_with_imports

    result = upgrade_tenant_schema(engine, schema)
    assert result.status is TenantStatus.SUCCESS, result.detail

    statuses = _statuses(engine, schema)
    assert statuses[sessions["awaiting"]] == "imported"
    assert statuses[sessions["signed_awaiting"]] == "pending_review"
    assert statuses[sessions["signed"]] == "finalized"
    assert statuses[sessions["recorded"]] == "pending_review"
