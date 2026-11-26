# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Schema setup and catalog assertions shared by applier tests."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from app.db.rls_apply import (
    DesiredTable,
    apply_tenant_rls,
    desired_from_sql,
)
from sqlalchemy import text
from sqlalchemy.orm import Session as OrmSession

if TYPE_CHECKING:
    from app.db import RlsReconcileCounts
    from sqlalchemy.engine import Engine

_TEMPLATE = Path(__file__).resolve().parents[3] / "app" / "db" / "tenant_template.sql"
# Read at import, for the test that takes each table in turn: the list has to
# exist when the tests are collected. Reading the file touches no database.
_TEMPLATE_TABLES = sorted(desired_from_sql(_TEMPLATE.read_text()))

# Holds a grant on both seeded patients.
_TREATING = "3a7e1c82-45bd-5f19-8c62-0e4d7b3a9f15"
# A clinician in the same practice with a grant on neither.
_COLLEAGUE = "9c4f2b61-7d3e-5a80-b915-6e2a8d0c4f37"


# The directory policy is captured from the tables its functions read.
_DIRECTORY_POLICY = "rls_practice_directory_read"
_DIRECTORY_TABLES = {
    "appointment_types",
    "appointments",
    "clinician_profiles",
    "patient_clinicians",
    "patient_coverage",
    "patients",
}


def _new_schema(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


def _drop_schema(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _apply(engine: Engine, schema: str, desired: dict[str, DesiredTable]) -> RlsReconcileCounts:
    with OrmSession(engine) as session:
        counts = apply_tenant_rls(session, schema, desired)
        session.commit()
    return counts


def _ddl(engine: Engine, statement: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(statement))


def _patients_read_by(engine: Engine, schema: str, clinician: str) -> int:
    """How many patient rows *clinician* reads, with only their own id armed."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text("RESET app.current_patient_id"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": clinician})
        count = conn.execute(text("SELECT count(*) FROM patients")).scalar_one()
        conn.rollback()
    return int(count)


def _policies(engine: Engine, schema: str) -> dict[tuple[str, str], tuple[str, str, str, str]]:
    """Every policy in *schema*, keyed by (table, policy), schema name removed."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename, policyname, permissive, cmd, "
                "COALESCE(qual, ''), COALESCE(with_check, '') "
                "FROM pg_policies WHERE schemaname = :s"
            ),
            {"s": schema},
        ).all()

    def strip(expr: str) -> str:
        return expr.replace(f'"{schema}".', "").replace(f"{schema}.", "")

    return {
        (table, policy): (permissive, cmd, strip(qual), strip(with_check))
        for table, policy, permissive, cmd, qual, with_check in rows
    }


def _policy_oids(engine: Engine, schema: str) -> dict[tuple[str, str], int]:
    """Each policy's OID. Dropping and creating a policy gives it a new one."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, p.polname, p.oid FROM pg_policy p "
                "JOIN pg_class c ON c.oid = p.polrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :s"
            ),
            {"s": schema},
        ).all()
    return {(table, policy): int(oid) for table, policy, oid in rows}


def _flags(engine: Engine, schema: str) -> dict[str, tuple[bool, bool]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relkind = 'r'"
            ),
            {"s": schema},
        ).all()
    return {name: (bool(enabled), bool(forced)) for name, enabled, forced in rows}
