# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Schema setup and synthetic charts."""

from __future__ import annotations

import importlib.util
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from types import ModuleType

    from sqlalchemy.engine import Connection, Engine

_ROLE = "pablo_practice_directory"
# The tenant revision before the one that adds the directory.
_PARENT_REVISION = "a9c3e7d2b518"
#: The main calendar an import's answers are remembered under.
_MAIN = "clinician@example.test"
_CALENDAR = f"calendar:{_MAIN}"


_A = "0b5c2e81-3f6d-5a47-9c18-4e7d2a90b1c3"
_B = "7e14a9d2-6c0b-5f83-a2d5-91b6c3e8f047"
_FAMILY = "family@example.com"
_COLUMNS = ["id", "first_name", "last_name", "date_of_birth", "email", "clinician_ids"]


def _new_schema(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


def _drop(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _as(engine: Engine, schema: str, user_id: str | None) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    if user_id is None:
        conn.execute(text("RESET app.current_user_id"))
    else:
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
    return conn


@dataclass(frozen=True)
class _Chart:
    first: str
    last: str
    email: str | None = None
    status: str = "active"
    deleted: bool = False


def _chart(engine: Engine, schema: str, clinician: str, chart: _Chart) -> str:
    """A chart created by ``clinician``, holding its primary grant, as the app writes it."""
    patient_id = str(uuid.uuid4())
    conn = _as(engine, schema, clinician)
    try:
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, email, status, session_count, date_of_birth, deleted_at, "
                "created_at, updated_at) VALUES (CAST(:pid AS uuid), :first, :last, "
                "lower(:first), lower(:last), :email, :status, 0, DATE '1980-01-02', "
                "CASE WHEN :deleted THEN now() END, now(), now())"
            ),
            {
                "pid": patient_id,
                "first": chart.first,
                "last": chart.last,
                "email": chart.email,
                "status": chart.status,
                "deleted": chart.deleted,
            },
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": clinician},
        )
        conn.commit()
    finally:
        conn.close()
    return patient_id


def _directory(conn: Connection) -> dict[str, list[str]]:
    rows = conn.execute(text("SELECT id, clinician_ids FROM practice_client_directory()")).all()
    return {str(r.id): sorted(str(u) for u in r.clinician_ids) for r in rows}


def _platform_revision() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic_platform"
        / "versions"
        / "d4a7e2c91b06_practice_directory_role.py"
    )
    spec = importlib.util.spec_from_file_location("practice_directory_role_revision", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
