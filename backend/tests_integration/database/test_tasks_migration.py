# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: compliance items become tasks, and nobody's are lost.

``d7a3f19c4e25`` renames ``compliance_items`` to ``tasks`` and adds the task
columns. This builds a practice at the parent revision holding a compliance
item with an evidence document and a supervision review hanging off it, runs
the real migration, and checks that:

* the item is still there, under the same id, as a compliance task entered
  by hand, with none of the new links set;
* what pointed at it still does — the document and the supervision review
  survive and their foreign keys now name ``tasks``;
* the renamed constraint and indexes carry the names a freshly provisioned
  practice has, so the two kinds of schema do not drift;
* the row policy is still on the table and still keyed on its owner;
* the downgrade puts the old table back with the row in it.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import _alembic_config_for
from app.db.provisioning import create_practice_schema, ensure_schemas
from sqlalchemy import create_engine, text

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_PARENT = "c5e2a8f71d94"
_UNDER_TEST = "d7a3f19c4e25"
_USER_ID = "11111111-2222-3333-4444-555555555555"
_ITEM_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_DOC_ID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
_REVIEW_ID = "cccccccc-dddd-eeee-ffff-000000000000"

_TABLES = ("compliance_items", "tasks", "compliance_documents", "supervision_relationships")


@pytest.fixture
def engine():
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    return eng


def _set_rls(conn, schema: str, *, on: bool) -> None:
    """Toggle the row policy on whichever of the tables exist, to seed and read past it."""
    for table in _TABLES:
        exists = conn.execute(
            text("SELECT to_regclass(:name)"), {"name": f"{schema}.{table}"}
        ).scalar()
        if exists is None:
            continue
        if on:
            conn.execute(text(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY"))
        else:
            conn.execute(text(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY"))
            conn.execute(text(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY"))


def _migrate(engine, schema: str, *, to: str, down: bool = False) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        if down:
            command.downgrade(cfg, to)
        else:
            command.upgrade(cfg, to)


def _query(engine, schema: str, sql: str, params: dict | None = None):
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, on=False)
        try:
            return conn.execute(text(sql), params or {}).all()
        finally:
            _set_rls(conn, schema, on=True)


@pytest.fixture
def tenant_at_parent(engine):
    """A practice at ``_PARENT`` with a licence, its evidence, and a supervision review."""
    schema = f"practice_test_tasks_mig_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    _migrate(engine, schema, to=_PARENT, down=True)

    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, on=False)
        conn.execute(
            text(
                "INSERT INTO compliance_items "
                "(id, user_id, item_type, label, due_date, notes, created_at, updated_at) "
                "VALUES (:id, :user, 'license', 'NY LMHC', :due, 'Board of mental health', "
                ":now, :now)"
            ),
            {"id": _ITEM_ID, "user": _USER_ID, "due": date(2027, 6, 30), "now": now},
        )
        conn.execute(
            text(
                "INSERT INTO compliance_documents (id, compliance_item_id, filename, mime_type, "
                "size_bytes, storage_uri, document_type, uploaded_at, uploaded_by_user_id) "
                "VALUES (:id, :item, 'license.pdf', 'application/pdf', 10, 'mem://x', "
                "'license', :now, :user)"
            ),
            {"id": _DOC_ID, "item": _ITEM_ID, "now": now, "user": _USER_ID},
        )
        conn.execute(
            text(
                "INSERT INTO supervision_relationships (id, user_id, compliance_item_id, "
                "relationship_type, supervisor_name, status, created_at, updated_at) "
                "VALUES (:id, :user, :item, 'clinical_supervision', 'Dr. Supervisor', 'active', "
                ":now, :now)"
            ),
            {"id": _REVIEW_ID, "user": _USER_ID, "item": _ITEM_ID, "now": now},
        )
        _set_rls(conn, schema, on=True)

    yield schema

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    script = ScriptDirectory.from_config(_alembic_config_for("practice"))
    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_existing_compliance_items_become_compliance_tasks(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    _migrate(engine, schema, to=_UNDER_TEST)

    [row] = _query(
        engine,
        schema,
        "SELECT id::text, user_id::text, item_type, label, due_date, notes, category, source, "
        "parent_task_id, goal_key, repeat_rule FROM tasks",
    )
    assert tuple(row) == (
        _ITEM_ID,
        _USER_ID,
        "license",
        "NY LMHC",
        date(2027, 6, 30),
        "Board of mental health",
        "compliance",
        "manual",
        None,
        None,
        None,
    )
    assert _query(engine, schema, "SELECT to_regclass('compliance_items')") == [(None,)]

    # What pointed at the item points at the task, and still finds it.
    [(doc_item,)] = _query(
        engine, schema, "SELECT compliance_item_id::text FROM compliance_documents"
    )
    [(review_item,)] = _query(
        engine, schema, "SELECT compliance_item_id::text FROM supervision_relationships"
    )
    assert doc_item == review_item == _ITEM_ID
    referenced = {
        r[0]
        for r in _query(
            engine,
            schema,
            "SELECT confrelid::regclass::text FROM pg_constraint "
            "WHERE contype = 'f' AND conrelid IN "
            "('compliance_documents'::regclass, 'supervision_relationships'::regclass) "
            "AND conname LIKE '%compliance_item_id_fkey'",
        )
    }
    assert referenced == {"tasks"}


def test_names_match_a_freshly_provisioned_practice(engine, tenant_at_parent) -> None:
    migrated = tenant_at_parent
    # To head, not just this revision: a fresh practice is built at head, so a
    # later revision's index on ``tasks`` must be compared on both sides too.
    _migrate(engine, migrated, to="head")
    fresh = f"practice_test_tasks_fresh_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, fresh)
    try:

        def names(schema: str) -> set[str]:
            indexes = _query(
                engine,
                schema,
                "SELECT indexname FROM pg_indexes WHERE schemaname = :s AND tablename = 'tasks'",
                {"s": schema},
            )
            constraints = _query(
                engine,
                schema,
                "SELECT conname FROM pg_constraint WHERE conrelid = 'tasks'::regclass",
            )
            return {r[0] for r in indexes} | {r[0] for r in constraints}

        assert names(migrated) == names(fresh)
        assert {"tasks_pkey", "ix_tasks_user_id", "ux_tasks_source_ref"} <= names(fresh)
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {fresh} CASCADE"))


def test_the_row_policy_survives_the_rename(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    _migrate(engine, schema, to=_UNDER_TEST)
    with engine.connect() as conn:
        forced = conn.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f"{schema}.tasks"},
        ).one()
        policies = conn.execute(
            text("SELECT qual FROM pg_policies WHERE schemaname = :s AND tablename = 'tasks'"),
            {"s": schema},
        ).all()
    assert tuple(forced) == (True, True)
    assert policies, "the renamed table has no row policy"
    assert all("user_id" in (p[0] or "") for p in policies)


def test_downgrade_puts_the_old_table_back_with_its_rows(engine, tenant_at_parent) -> None:
    schema = tenant_at_parent
    _migrate(engine, schema, to=_UNDER_TEST)
    _migrate(engine, schema, to=_PARENT, down=True)

    assert _query(engine, schema, "SELECT to_regclass('tasks')") == [(None,)]
    [(item_id, label)] = _query(engine, schema, "SELECT id::text, label FROM compliance_items")
    assert (item_id, label) == (_ITEM_ID, "NY LMHC")
    columns = {
        r[0]
        for r in _query(
            engine,
            schema,
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = :s AND table_name = 'compliance_items'",
            {"s": schema},
        )
    }
    assert not columns & {"category", "source", "parent_task_id", "goal_key", "repeat_rule"}
    names = {
        r[0]
        for r in _query(
            engine,
            schema,
            "SELECT indexname FROM pg_indexes WHERE schemaname = :s "
            "AND tablename = 'compliance_items'",
            {"s": schema},
        )
    }
    assert names == {
        "compliance_items_pkey",
        "ix_compliance_items_user_id",
        "ux_compliance_items_source_ref",
    }


def test_upgrade_runs_twice(engine, tenant_at_parent) -> None:
    """Idempotent, like the chain: a re-run over a migrated practice changes nothing."""
    schema = tenant_at_parent
    _migrate(engine, schema, to=_UNDER_TEST)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.stamp(cfg, _PARENT)
    _migrate(engine, schema, to=_UNDER_TEST)
    assert len(_query(engine, schema, "SELECT id FROM tasks")) == 1
