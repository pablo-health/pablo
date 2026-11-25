# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: a thread read before ``b8e3f1a6d924`` stays read.

The revision replaces the thread's one ``clinician_last_read_at`` with a read
marker per clinician. This builds a tenant at the parent revision with a read
thread, an unread one, and grants that are live, expired and absent, runs the
real migration, and reads back who got a marker.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA, enable_rls_on_schema
from app.db.migrate_tenants import TenantStatus, _alembic_config_for, upgrade_tenant_schema
from app.db.provisioning import _ALEMBIC_INI_PATH, create_practice_schema, ensure_schemas
from app.repositories.postgres.patient_message import PostgresPatientMessageRepository
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

#: The revision immediately before the one under test.
_PARENT = "8c5e65a0e56a"
_UNDER_TEST = "b8e3f1a6d924"

_PRIMARY = "2a7d4c91-5e38-4f06-b1c2-8d9e0f3a6b47"
_CO_TREATING = "7b1e9f32-c4a6-4d85-9e07-3f2a8c6d1b54"
_EXPIRED = "c3f8a2d6-1b74-4e9c-a5d0-6e2b9f4c7a18"
_NO_GRANT = "e9d4b7a3-6c21-4f58-8b3e-1a7c5d0f2e96"
_TABLES = ("patients", "patient_clinicians", "patient_message_threads", "patient_messages")
_T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
_READ_AT = _T0 + timedelta(hours=1)


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


def _thread(conn, patient_id: str, *, read_at: datetime | None) -> str:
    """A thread with one message from the client, read by the practice or not."""
    thread_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO patient_message_threads (id, patient_id, status, created_at,"
            " last_message_at, clinician_last_read_at)"
            " VALUES (:id, :pid, 'open', :ts, :ts, :read_at)"
        ),
        {"id": thread_id, "pid": patient_id, "ts": _T0, "read_at": read_at},
    )
    conn.execute(
        text(
            "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, created_at)"
            " VALUES (:id, :tid, :pid, 'patient', 'hello', :ts)"
        ),
        {"id": str(uuid.uuid4()), "tid": thread_id, "pid": patient_id, "ts": _T0},
    )
    return thread_id


@pytest.fixture
def tenant_with_threads(engine):
    """A tenant at ``_PARENT``: one client, three grants, a read and an unread thread."""
    schema = f"practice_test_thread_reads_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    patient_id = str(uuid.uuid4())
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
            {"id": patient_id, "ts": _T0},
        )
        for user_id, role, expires_at in (
            (_PRIMARY, "primary", None),
            (_CO_TREATING, "co_treating", None),
            (_EXPIRED, "covering", _T0),
        ):
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, role, granted_by,"
                    " expires_at) VALUES (:pid, :uid, :role, :uid, :exp)"
                ),
                {"pid": patient_id, "uid": user_id, "role": role, "exp": expires_at},
            )
        threads = {
            "read": _thread(conn, patient_id, read_at=_READ_AT),
            "unread": _thread(conn, patient_id, read_at=None),
        }
        for table in _TABLES:
            _set_rls(conn, schema, table, on=True)

    yield schema, threads

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def _markers(engine, schema: str) -> set[tuple[str, str, datetime]]:
    """Read past the row policy — the suite's role is NOBYPASSRLS."""
    table = "patient_message_thread_reads"
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, table, on=False)
        try:
            rows = conn.execute(
                text(f"SELECT thread_id::text, user_id::text, last_read_at FROM {table}")  # noqa: S608
            ).all()
        finally:
            _set_rls(conn, schema, table, on=True)
    return {(row[0], row[1], row[2]) for row in rows}


def _unread_counts(engine, schema: str, user_id: str) -> dict[str, int]:
    """What *user_id*'s inbox says, through the repository and the row policy."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
        repo = PostgresPatientMessageRepository(Session(bind=conn))
        return {row.thread.id: row.unread_count for row in repo.list_inbox_threads(user_id)}


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    """If the chain is re-pointed, the fixture must walk back to the new parent."""
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))

    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_a_read_thread_stays_read_for_every_clinician_with_access(
    engine, tenant_with_threads
) -> None:
    schema, threads = tenant_with_threads

    result = upgrade_tenant_schema(engine, schema)
    assert result.status is TenantStatus.SUCCESS, result.detail
    with Session(engine) as session:  # the reconcile the migrate job runs after the chain
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        enable_rls_on_schema(session, schema)
        session.commit()

    assert _markers(engine, schema) == {
        (threads["read"], _PRIMARY, _READ_AT),
        (threads["read"], _CO_TREATING, _READ_AT),
    }
    for clinician in (_PRIMARY, _CO_TREATING):
        assert _unread_counts(engine, schema, clinician) == {
            threads["read"]: 0,
            threads["unread"]: 1,
        }
    assert _unread_counts(engine, schema, _NO_GRANT) == {}
