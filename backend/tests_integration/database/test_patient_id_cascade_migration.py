# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: ``8c5e65a0e56a`` removes orphans, keeps the rest, and validates.

The revision adds ``patient_id -> patients(id) ON DELETE CASCADE`` to seven
tables that had no key, deleting first the rows whose client no longer exists.
Those orphans are real: the purge before ``PABLO-8gna.37`` deleted the client
and left these rows.

The dangerous mistake is the orphan check itself. The migration runs as a role
without BYPASSRLS against force-RLS tables, with no principal armed; to it,
``patients`` looks empty, so a naive ``NOT EXISTS (SELECT ... FROM patients)``
finds every row orphaned and deletes the lot. So every table here holds a row
for a client who exists as well as an orphan, and the test asserts the first
survives. An empty tenant would pass whatever the migration did.

The schema is provisioned at head and walked back to the parent with the
revision's own ``downgrade()``, as ``test_patient_charge_method_backfill_rls``
does, so the downgrade is exercised on the way in.
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

#: The seven tables the revision keys, in its order.
_KEYED = (
    "patient_intake_assignments",
    "patient_intake_submissions",
    "patient_charges",
    "patient_payment_methods",
    "companion_sessions",
    "companion_auth_challenges",
    "external_calendar_events",
)
#: Seeded too: the client, the intake form an assignment needs, and an answer
#: hanging off each assignment to show the orphan's children go with it.
_SEEDED = (
    "patients",
    "intake_packet_templates",
    "intake_packet_versions",
    "intake_item_definitions",
    "patient_intake_responses",
    *_KEYED,
)

_ROWS = (
    "INSERT INTO patient_intake_assignments (id, patient_id, version_id, status, "
    "assigned_at, updated_at) VALUES (CAST(:assignment AS uuid), CAST(:pid AS uuid), "
    "CAST(:version AS uuid), 'submitted', now(), now())",
    "INSERT INTO patient_intake_responses (id, assignment_id, patient_id, item_id, value, "
    "created_at, updated_at) VALUES (gen_random_uuid(), CAST(:assignment AS uuid), "
    'CAST(:pid AS uuid), CAST(:item AS uuid), CAST(\'{"text": "an answer"}\' AS jsonb), '
    "now(), now())",
    "INSERT INTO patient_intake_submissions (id, patient_id, submitted_at, payload, "
    "created_by, created_at) VALUES (CAST(gen_random_uuid() AS text), CAST(:pid AS uuid), "
    "now(), CAST('{}' AS jsonb), :u, now())",
    "INSERT INTO patient_charges (id, patient_id, amount_cents, status, kind, method, "
    "created_by_user_id, created_at) VALUES (CAST(gen_random_uuid() AS text), "
    "CAST(:pid AS uuid), 15000, 'succeeded', 'session', 'card', :u, now())",
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


#: Portal sign-in tables carry no row security at all; leave their flags alone.
_NOT_ROW_SCOPED = frozenset({"companion_sessions", "companion_auth_challenges"})


def _as_owner(conn: Connection, schema: str, tables: tuple[str, ...]) -> None:
    """Read and write as the schema owner, bypassing row security for this test's setup.

    The migration is what runs under forced RLS; the setup and the checks
    must see the truth. Restored by ``_force``.
    """
    conn.execute(text(f"SET LOCAL search_path = {schema}, {PLATFORM_SCHEMA}, public"))
    for table in tables:
        if table not in _NOT_ROW_SCOPED:
            conn.execute(text(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY"))


def _force(conn: Connection, tables: tuple[str, ...]) -> None:
    for table in tables:
        if table not in _NOT_ROW_SCOPED:
            conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))


def _rls_flags(engine: Engine, schema: str) -> dict[str, tuple[bool, bool]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity"
                " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relname = ANY(:t)"
            ),
            {"s": schema, "t": ["patients", *_KEYED]},
        ).all()
    return {name: (bool(enabled), bool(forced)) for name, enabled, forced in rows}


def _keys(engine: Engine, schema: str) -> dict[str, bool]:
    """``<table>_patient_id_fkey`` per keyed table that has one, mapped to validated."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, con.convalidated FROM pg_constraint con"
                " JOIN pg_class c ON con.conrelid = c.oid"
                " JOIN pg_namespace n ON c.relnamespace = n.oid"
                " WHERE n.nspname = :s AND con.contype = 'f'"
                " AND con.conname = c.relname || '_patient_id_fkey'"
                " AND c.relname = ANY(:t)"
            ),
            {"s": schema, "t": list(_KEYED)},
        ).all()
    return {name: bool(valid) for name, valid in rows}


def _counts(engine: Engine, schema: str, patient_id: str) -> dict[str, tuple[int, int]]:
    """Per table: (rows for ``patient_id``, rows for anyone else)."""
    tables = (*_KEYED, "patient_intake_responses")
    out: dict[str, tuple[int, int]] = {}
    with engine.begin() as conn:
        _as_owner(conn, schema, tables)
        for table in tables:
            mine, other = conn.execute(
                text(
                    f"SELECT count(*) FILTER (WHERE patient_id = CAST(:p AS uuid)),"  # noqa: S608
                    f" count(*) FILTER (WHERE patient_id <> CAST(:p AS uuid)) FROM {table}"
                ),
                {"p": patient_id},
            ).one()
            out[table] = (int(mine), int(other))
        _force(conn, tables)
    return out


@pytest.fixture
def parent_schema(engine: Engine) -> Iterator[tuple[str, str]]:
    """A practice at the parent revision: one client's rows plus an orphan of each.

    Yields (schema, the existing client's id).
    """
    schema = f"practice_test_pidfk_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    form = {name: str(uuid.uuid4()) for name in ("template", "version", "item")}
    client = str(uuid.uuid4())
    with engine.begin() as conn:
        _as_owner(conn, schema, _SEEDED)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:id AS uuid), 'Test', 'Client', 'test', 'client', 'active', 0, "
                "now(), now())"
            ),
            {"id": client},
        )
        conn.execute(
            text(
                "INSERT INTO intake_packet_templates (id, name, created_at) "
                "VALUES (CAST(:id AS uuid), 'New client', now())"
            ),
            {"id": form["template"]},
        )
        conn.execute(
            text(
                "INSERT INTO intake_packet_versions (id, template_id, version, created_at) "
                "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), 1, now())"
            ),
            {"id": form["version"], "t": form["template"]},
        )
        conn.execute(
            text(
                "INSERT INTO intake_item_definitions (id, version_id, key, position, item_type) "
                "VALUES (CAST(:id AS uuid), CAST(:v AS uuid), 'reason', 0, 'free_text')"
            ),
            {"id": form["item"], "v": form["version"]},
        )
        # The orphan's client id names no patients row: what the old purge left.
        for pid in (client, str(uuid.uuid4())):
            params = {
                "pid": pid,
                "u": _CLINICIAN,
                "assignment": str(uuid.uuid4()),
                "version": form["version"],
                "item": form["item"],
            }
            for statement in _ROWS:
                conn.execute(text(statement), params)
        _force(conn, _SEEDED)

    yield schema, client

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_precondition_each_table_holds_a_client_row_and_an_orphan(
    engine: Engine, parent_schema: tuple[str, str]
) -> None:
    schema, client = parent_schema
    assert _keys(engine, schema) == {}, "the parent revision has none of these keys"
    counts = _counts(engine, schema, client)
    assert counts == dict.fromkeys(counts, (1, 1))


def test_upgrade_deletes_orphans_keeps_the_clients_rows_and_validates(
    engine: Engine, parent_schema: tuple[str, str]
) -> None:
    schema, client = parent_schema
    flags_before = _rls_flags(engine, schema)

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    counts = _counts(engine, schema, client)
    assert counts == dict.fromkeys(counts, (1, 0)), (
        "orphans (and an orphan assignment's answer) go; the existing client's rows stay"
    )
    assert _keys(engine, schema) == dict.fromkeys(_KEYED, True)
    assert _rls_flags(engine, schema) == flags_before


def test_the_new_keys_cascade_from_the_client(
    engine: Engine, parent_schema: tuple[str, str]
) -> None:
    schema, client = parent_schema
    assert upgrade_tenant_schema(engine, schema).status is TenantStatus.SUCCESS

    with engine.begin() as conn:
        _as_owner(conn, schema, ("patients",))
        conn.execute(text("DELETE FROM patients WHERE id = CAST(:p AS uuid)"), {"p": client})
        _force(conn, ("patients",))

    counts = _counts(engine, schema, client)
    assert counts == dict.fromkeys(counts, (0, 0))


def test_upgrade_is_idempotent_and_downgrade_walks_back(
    engine: Engine, parent_schema: tuple[str, str]
) -> None:
    schema, client = parent_schema
    assert upgrade_tenant_schema(engine, schema).status is TenantStatus.SUCCESS

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    assert _keys(engine, schema) == {}
    with engine.connect() as conn:
        index = conn.execute(
            text("SELECT to_regclass(:n)"),
            {"n": f"{schema}.ix_external_calendar_events_patient_id"},
        ).scalar()
    assert index is None
    assert _counts(engine, schema, client) == dict.fromkeys(
        (*_KEYED, "patient_intake_responses"), (1, 0)
    )

    assert upgrade_tenant_schema(engine, schema).status is TenantStatus.SUCCESS
    assert _keys(engine, schema) == dict.fromkeys(_KEYED, True)
