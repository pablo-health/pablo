# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: ``8c5e65a0e56a`` keys patient_id to patients, through forced RLS.

The cases are the ones row security could get wrong. A tenant whose rows all
name a real client takes the keys with ``patients`` under FORCE, which the
migration role cannot see through unaided. A tenant with an orphan in a
forced table fails by table name and keeps everything. Afterwards, deleting
the client empties all seven tables.

Each schema is provisioned at head and walked back to the parent with the
revision's own ``downgrade()``, so the downgrade runs too.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from alembic import command
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import TenantStatus, _alembic_config_for, upgrade_tenant_schema
from app.db.provisioning import create_practice_schema
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_PARENT = "16d6543b3c6f"
_CLINICIAN = "6a1f0c2e-7b3d-4e5f-8a9b-0c1d2e3f4a5b"
_KEYED = (
    "patient_intake_assignments",
    "patient_intake_submissions",
    "patient_charges",
    "patient_payment_methods",
    "companion_sessions",
    "companion_auth_challenges",
    "external_calendar_events",
)
#: Portal sign-in tables have no row security; every other table here is forced.
_NOT_ROW_SCOPED = frozenset({"companion_sessions", "companion_auth_challenges"})
_FORCED = tuple(
    t
    for t in ("patients", "intake_packet_templates", "intake_packet_versions", *_KEYED)
    if t not in _NOT_ROW_SCOPED
)

_CHARGE = (
    "INSERT INTO patient_charges (id, patient_id, amount_cents, status, kind, method, "
    "created_by_user_id, created_at) VALUES (CAST(gen_random_uuid() AS text), "
    "CAST(:pid AS uuid), 15000, 'succeeded', 'session', 'card', :u, now())"
)
_ROWS = (
    "INSERT INTO patients (id, first_name, last_name, first_name_lower, last_name_lower, "
    "status, session_count, created_at, updated_at) VALUES (CAST(:pid AS uuid), 'Test', "
    "'Client', 'test', 'client', 'active', 0, now(), now())",
    "INSERT INTO intake_packet_templates (id, name, created_at) "
    "VALUES (CAST(:template AS uuid), 'New client', now())",
    "INSERT INTO intake_packet_versions (id, template_id, version, created_at) "
    "VALUES (CAST(:version AS uuid), CAST(:template AS uuid), 1, now())",
    "INSERT INTO patient_intake_assignments (id, patient_id, version_id, status, "
    "assigned_at, updated_at) VALUES (gen_random_uuid(), CAST(:pid AS uuid), "
    "CAST(:version AS uuid), 'submitted', now(), now())",
    "INSERT INTO patient_intake_submissions (id, patient_id, submitted_at, payload, "
    "created_by, created_at) VALUES (CAST(gen_random_uuid() AS text), CAST(:pid AS uuid), "
    "now(), CAST('{}' AS jsonb), :u, now())",
    _CHARGE,
    "INSERT INTO patient_payment_methods (id, patient_id, stripe_customer_id, "
    "created_by_user_id, created_at) VALUES (CAST(gen_random_uuid() AS text), "
    "CAST(:pid AS uuid), 'cus_test', :u, now())",
    "INSERT INTO companion_sessions (jti, patient_id, issued_at, expires_at, "
    "chain_started_at) VALUES (CAST(gen_random_uuid() AS text), CAST(:pid AS uuid), "
    "now(), now() + interval '1 hour', now())",
    "INSERT INTO companion_auth_challenges (jti, patient_id, created_at, expires_at) "
    "VALUES (CAST(gen_random_uuid() AS text), CAST(:pid AS uuid), now(), "
    "now() + interval '10 minutes')",
    "INSERT INTO external_calendar_events (id, user_id, source, source_event_id, start_at, "
    "end_at, title, answer, patient_id) VALUES (gen_random_uuid(), CAST(:u AS uuid), "
    "'ical:test', CAST(gen_random_uuid() AS text), now(), now() + interval '50 minutes', "
    "'Session', 'client', CAST(:pid AS uuid))",
)


def _set_force(conn: Connection, schema: str, *, on: bool) -> None:
    """Test setup and checks read as the schema owner, so FORCE is off around them."""
    conn.execute(text(f"SET LOCAL search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    for table in _FORCED:
        conn.execute(text(f"ALTER TABLE {table} {'' if on else 'NO '}FORCE ROW LEVEL SECURITY"))


def _forced(engine: Engine, schema: str) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relforcerowsecurity"
            ),
            {"s": schema},
        )
        return set(rows.scalars())


def _keys(engine: Engine, schema: str) -> set[str]:
    """Keyed tables that have a validated ``<table>_patient_id_fkey``."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname FROM pg_constraint con JOIN pg_class c ON c.oid = con.conrelid"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND con.convalidated"
                " AND con.conname = c.relname || '_patient_id_fkey' AND c.relname = ANY(:t)"
            ),
            {"s": schema, "t": list(_KEYED)},
        )
        return set(rows.scalars())


def _counts(engine: Engine, schema: str) -> dict[str, int]:
    with engine.begin() as conn:
        _set_force(conn, schema, on=False)
        counts = {
            t: int(conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one())  # noqa: S608
            for t in _KEYED
        }
        _set_force(conn, schema, on=True)
    return counts


@pytest.fixture
def parent_schema(engine: Engine) -> Iterator[str]:
    """A practice at the parent revision with one client's row in every keyed table."""
    schema = f"practice_test_pidfk_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)
    params = {
        "pid": str(uuid.uuid4()),
        "u": _CLINICIAN,
        "template": str(uuid.uuid4()),
        "version": str(uuid.uuid4()),
    }
    with engine.begin() as conn:
        _set_force(conn, schema, on=False)
        for statement in _ROWS:
            conn.execute(text(statement), params)
        _set_force(conn, schema, on=True)

    yield schema

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_keys_go_on_through_forced_rls_and_every_row_stays(
    engine: Engine, parent_schema: str
) -> None:
    forced_before = _forced(engine, parent_schema)
    assert "patients" in forced_before
    assert _keys(engine, parent_schema) == set()

    result = upgrade_tenant_schema(engine, parent_schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    # Before _counts, which re-forces the tables itself on its way out.
    assert _forced(engine, parent_schema) == forced_before
    assert _keys(engine, parent_schema) == set(_KEYED)
    assert _counts(engine, parent_schema) == dict.fromkeys(_KEYED, 1)


def test_an_orphan_in_a_forced_table_fails_by_name_and_nothing_changes(
    engine: Engine, parent_schema: str
) -> None:
    with engine.begin() as conn:
        _set_force(conn, parent_schema, on=False)
        conn.execute(text(_CHARGE), {"pid": str(uuid.uuid4()), "u": _CLINICIAN})
        _set_force(conn, parent_schema, on=True)
    forced_before = _forced(engine, parent_schema)

    result = upgrade_tenant_schema(engine, parent_schema)

    assert result.status is TenantStatus.FAILED
    assert result.detail == (
        "patient_charges has rows whose patient_id names no client; delete them and re-run"
    )
    assert _keys(engine, parent_schema) == set()
    assert _counts(engine, parent_schema)["patient_charges"] == 2
    assert _forced(engine, parent_schema) == forced_before


def test_deleting_the_client_empties_every_keyed_table(engine: Engine, parent_schema: str) -> None:
    assert upgrade_tenant_schema(engine, parent_schema).status is TenantStatus.SUCCESS

    with engine.begin() as conn:
        _set_force(conn, parent_schema, on=False)
        conn.execute(text("DELETE FROM patients"))
        _set_force(conn, parent_schema, on=True)

    assert _counts(engine, parent_schema) == dict.fromkeys(_KEYED, 0)


def test_a_second_run_over_existing_keys_is_a_no_op(engine: Engine, parent_schema: str) -> None:
    assert upgrade_tenant_schema(engine, parent_schema).status is TenantStatus.SUCCESS
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {parent_schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(parent_schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = parent_schema
        command.stamp(cfg, _PARENT)

    result = upgrade_tenant_schema(engine, parent_schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    assert _keys(engine, parent_schema) == set(_KEYED)
