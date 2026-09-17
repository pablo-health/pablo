# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: the payment-method backfill reaches rows it cannot see.

``c1b7e4a92d53`` adds ``patient_charges.method``, backfills it for the
collecting kinds, and then constrains the two to agree. The backfill and the
constraint do not read the table the same way: an ``UPDATE`` is filtered by row
level security, and ``ADD CONSTRAINT``'s validation scan is not. Per-tenant
tables are provisioned ``FORCE ROW LEVEL SECURITY``, the migration runs as a
role without BYPASSRLS, and ``env.py`` sets no session GUC — so before the fix
the ``UPDATE`` matched nothing, silently, and the constraint then rejected every
row the ``UPDATE`` had skipped.

That is invisible on an empty tenant, which is why it survived a fan-out across
157 schemas: the 156 that passed had no charges at all, and the one holding 67
session charges aborted the whole run.

So this test is only meaningful with rows in the table. It builds the state the
fan-out actually meets — a populated schema, at the revision before this one,
with RLS forced — and runs the real migration against it. The role is already
``NOSUPERUSER NOBYPASSRLS`` here (see ``tests_integration/conftest.py``, which
creates it that way precisely so RLS applies), so no further arrangement is
needed to reproduce the failure.
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
from app.db.migrate_tenants import (
    TenantStatus,
    _alembic_config_for,
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
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres. "
        "Start proxy with: make db-dev-proxy"
    ),
)

#: The revision immediately before the one under test.
_PARENT = "e3a9c7b1d802"
_UNDER_TEST = "c1b7e4a92d53"


@pytest.fixture
def engine():
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    return eng


@pytest.fixture
def populated_tenant(engine):
    """A tenant at ``_PARENT`` holding one session charge, RLS forced.

    Provisioning applies the template, which is captured at head — so the
    schema is walked back to the parent revision with the migration's own
    ``downgrade()`` rather than by hand-editing DDL.
    """
    schema = f"practice_test_chargerls_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    patient_id = uuid.uuid4()
    charge_id = f"ch_{uuid.uuid4().hex[:16]}"
    now = datetime.now(UTC)

    # Seeding is the precondition, not the thing under test. The app writes
    # these rows with the policy GUC armed; here RLS is stood down for the
    # insert and immediately restored, so the migration still meets a table it
    # cannot read — which is the whole point.
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, on=False)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower,"
                " last_name_lower, status, session_count, created_at, updated_at)"
                " VALUES (:id, 'Test', 'Charge', 'test', 'charge', 'active', 0, :ts, :ts)"
            ),
            {"id": patient_id, "ts": now},
        )
        conn.execute(
            text(
                "INSERT INTO patient_charges (id, patient_id, amount_cents, currency,"
                " status, created_by_user_id, created_at, kind)"
                " VALUES (:id, :pid, 15000, 'usd', 'succeeded', 'seed', :ts, 'session')"
            ),
            {"id": charge_id, "pid": patient_id, "ts": now},
        )
        _set_rls(conn, schema, on=True)

    yield schema, charge_id

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))


def _set_rls(conn, schema: str, *, on: bool) -> None:
    stmt = "ENABLE ROW LEVEL SECURITY" if on else "NO FORCE ROW LEVEL SECURITY"
    conn.execute(text(f"ALTER TABLE {schema}.patients {stmt}"))
    conn.execute(text(f"ALTER TABLE {schema}.patient_charges {stmt}"))
    if on:
        conn.execute(text(f"ALTER TABLE {schema}.patients FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.patient_charges FORCE ROW LEVEL SECURITY"))
    else:
        conn.execute(text(f"ALTER TABLE {schema}.patients DISABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.patient_charges DISABLE ROW LEVEL SECURITY"))


def _rls_flags(engine, schema: str, table: str) -> tuple[bool, bool]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity"
                " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relname = :t"
            ),
            {"s": schema, "t": table},
        ).one()
    return bool(row[0]), bool(row[1])


def _read_through_rls(engine, schema: str, sql: str, params: dict):
    """Read a per-tenant table without arming the policy GUC.

    The suite's role is NOBYPASSRLS, so an ordinary SELECT here would return
    nothing and every assertion below would pass vacuously.
    """
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, on=False)
        try:
            return conn.execute(text(sql), params).all()
        finally:
            _set_rls(conn, schema, on=True)


def test_precondition_backfill_target_is_invisible_to_the_migration(
    engine, populated_tenant
) -> None:
    """The seeded row exists and the migration's role cannot see it.

    Without this the main test could pass for the wrong reason — an empty
    table satisfies the constraint trivially, which is exactly how the bug
    stayed hidden on 156 tenants.
    """
    schema, _ = populated_tenant

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        visible = conn.execute(text("SELECT count(*) FROM patient_charges")).scalar()

    truth = _read_through_rls(engine, schema, "SELECT count(*) FROM patient_charges", {})

    assert visible == 0, "policy-filtered read should see nothing without the GUC"
    assert truth[0][0] == 1, "the row is really there"


def test_migration_backfills_rows_it_cannot_see_and_succeeds(engine, populated_tenant) -> None:
    """The case that failed the fan-out: a populated, RLS-forced tenant."""
    schema, charge_id = populated_tenant

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail

    rows = _read_through_rls(
        engine,
        schema,
        "SELECT method FROM patient_charges WHERE id = :id",
        {"id": charge_id},
    )
    assert rows[0][0] == "card", (
        "the pre-existing session charge must be backfilled, not merely "
        "un-rejected by there being no rows to check"
    )


def test_migration_restores_the_rls_state_it_found(engine, populated_tenant) -> None:
    """Suspension is a means, not a side effect the tenant keeps."""
    schema, _ = populated_tenant

    before = _rls_flags(engine, schema, "patient_charges")
    assert before == (True, True), "precondition: provisioning forces RLS"

    result = upgrade_tenant_schema(engine, schema)
    assert result.status is TenantStatus.SUCCESS, result.detail

    assert _rls_flags(engine, schema, "patient_charges") == before


def test_parent_revision_is_still_the_one_this_fixture_assumes() -> None:
    """Guard the fixture against the chain moving underneath it.

    ``populated_tenant`` walks back to ``_PARENT`` by name. If
    ``_UNDER_TEST``'s ``down_revision`` is ever re-pointed — routine when
    landing order changes — that downgrade would stop just short of the
    migration under test, the table would arrive already constrained, and
    every assertion here would pass without exercising anything.
    """
    script = ScriptDirectory.from_config(Config(str(_ALEMBIC_INI_PATH)))

    assert script.get_revision(_UNDER_TEST).down_revision == _PARENT


def test_the_upgrade_really_applies_the_revision_under_test(engine, populated_tenant) -> None:
    """The columns this revision adds are absent before and present after."""
    schema, _ = populated_tenant

    with engine.connect() as conn:
        start = conn.execute(
            text(f"SELECT version_num FROM {schema}.alembic_version")  # noqa: S608
        ).scalar()
    assert start == _PARENT

    upgrade_tenant_schema(engine, schema)

    cols = _read_through_rls(
        engine,
        schema,
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = :s AND table_name = 'patient_charges'"
        "   AND column_name IN ('method', 'payment_reference')",
        {"s": schema},
    )
    assert {c[0] for c in cols} == {"method", "payment_reference"}
