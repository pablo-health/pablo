# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The problem list and the allergy record, against a real provisioned tenant.

* Isolation: ``patient_problems`` takes the ``has_patient_access`` row policy
  from the reconcile pass, with no hand-written policy. A clinician with a
  grant reads and writes the list; one without reads nothing and cannot
  insert. Every invisibility check follows a control that the row is visible
  to the grantee, so an empty table cannot pass it.
* The derived line: every write rewrites ``patients.diagnosis``, and a new
  chart that arrives with a diagnosis starts its list from it.
* Allergies: the three states persist, and the database refuses a
  ``recorded`` status with no entries.
* The carry-over: ``c9f3a6e2d817`` turns each free-text diagnosis into one
  active problem, lifting a single code out of the text and leaving the code
  empty when there is none or more than one.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from alembic import command
from app.db import PLATFORM_SCHEMA
from app.db.migrate_tenants import TenantStatus, _alembic_config_for, upgrade_tenant_schema
from app.db.provisioning import create_practice_schema, ensure_schemas
from app.models import Patient
from app.problems.models import ProblemStatus
from app.problems.schemas import AddProblemRequest, UpdateProblemRequest
from app.problems.service import ProblemService
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.patient_problem import PostgresPatientProblemRepository
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason="PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres.",
)

_CLINICIAN_A = "b7bfbadb-c5da-505c-82d9-7609faf00be1"
_CLINICIAN_B = "73738015-ff11-5dc3-81d1-28314d804335"
_PARENT = "b4e8d1c7a925"
_UNDER_TEST = "c9f3a6e2d817"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    eng = create_engine(_db_url, pool_pre_ping=True)
    ensure_schemas(eng)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[str]:
    schema = f"practice_test_problems_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


@contextmanager
def _session(engine: Engine, schema: str, user_id: str) -> Iterator[Session]:
    """A session on one held connection, so a commit keeps its search_path and user."""
    with engine.connect() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
        conn.commit()
        with Session(bind=conn) as session:
            yield session


def _new_patient(engine: Engine, schema: str, diagnosis: str | None = None) -> str:
    """A chart created through the repository, granted to clinician A."""
    now = datetime.now(UTC)
    patient = Patient(
        id=str(uuid.uuid4()),
        first_name="Sam",
        last_name="Sample",
        created_at=now,
        updated_at=now,
        diagnosis=diagnosis,
    )
    with _session(engine, schema, _CLINICIAN_A) as session:
        PostgresPatientRepository(session).create(patient, _CLINICIAN_A)
        session.commit()
    return patient.id


def _diagnosis(engine: Engine, schema: str, patient_id: str) -> str | None:
    with _session(engine, schema, _CLINICIAN_A) as session:
        patient = PostgresPatientRepository(session).get(patient_id, _CLINICIAN_A)
        assert patient is not None
        return patient.diagnosis


def test_grantee_reads_and_writes_the_list_and_an_outsider_sees_nothing(
    engine: Engine, tenant: str
) -> None:
    patient_id = _new_patient(engine, tenant)

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = ProblemService(PostgresPatientProblemRepository(session))
        service.add(
            patient_id,
            _CLINICIAN_A,
            AddProblemRequest(label="Generalized anxiety disorder", icd10_code="F41.1"),
        )
        session.commit()
        assert [p.icd10_code for p in service.problems(patient_id)] == ["F41.1"], "control"

    with _session(engine, tenant, _CLINICIAN_B) as session:
        assert PostgresPatientProblemRepository(session).list_by_patient(patient_id) == []
        with pytest.raises(ProgrammingError, match="row-level security"):
            session.execute(
                text(
                    "INSERT INTO patient_problems "
                    "(id, patient_id, label, status, position, added_at, updated_at) "
                    "VALUES (gen_random_uuid(), CAST(:pid AS uuid), 'X', 'active', 9, now(), now())"
                ),
                {"pid": patient_id},
            )
        session.rollback()


def test_every_write_rewrites_the_derived_diagnosis(engine: Engine, tenant: str) -> None:
    patient_id = _new_patient(engine, tenant)
    assert _diagnosis(engine, tenant, patient_id) is None

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = ProblemService(PostgresPatientProblemRepository(session))
        gad = service.add(
            patient_id, _CLINICIAN_A, AddProblemRequest(label="GAD", icd10_code="F41.1")
        )
        mdd = service.add(
            patient_id, _CLINICIAN_A, AddProblemRequest(label="MDD", icd10_code="F33.1")
        )
        session.commit()
    assert _diagnosis(engine, tenant, patient_id) == "GAD (F41.1); MDD (F33.1)"

    with _session(engine, tenant, _CLINICIAN_A) as session:
        service = ProblemService(PostgresPatientProblemRepository(session))
        service.reorder(patient_id, [mdd.id, gad.id])
        service.update(patient_id, gad.id, UpdateProblemRequest(status="resolved"))
        session.commit()
        assert [p.status for p in service.problems(patient_id)] == ["active", "resolved"]
        assert service.visit_codes(patient_id) == ["F33.1"]
    assert _diagnosis(engine, tenant, patient_id) == "MDD (F33.1)"


def test_a_new_chart_with_a_diagnosis_starts_its_list(engine: Engine, tenant: str) -> None:
    patient_id = _new_patient(engine, tenant, diagnosis="F41.1 Generalized anxiety disorder")

    with _session(engine, tenant, _CLINICIAN_A) as session:
        [problem] = PostgresPatientProblemRepository(session).list_by_patient(patient_id)
    assert (problem.label, problem.icd10_code, problem.status) == (
        "Generalized anxiety disorder",
        "F41.1",
        ProblemStatus.ACTIVE,
    )
    assert problem.added_by == _CLINICIAN_A
    assert _diagnosis(engine, tenant, patient_id) == "Generalized anxiety disorder (F41.1)"


def test_allergies_persist_in_each_state_and_recorded_needs_an_entry(
    engine: Engine, tenant: str
) -> None:
    patient_id = _new_patient(engine, tenant)

    for status, entries in (
        ("nkda", []),
        ("recorded", [{"substance": "Penicillin", "reaction": "Hives"}]),
        ("not_recorded", []),
    ):
        with _session(engine, tenant, _CLINICIAN_A) as session:
            repo = PostgresPatientRepository(session)
            patient = repo.get(patient_id, _CLINICIAN_A)
            assert patient is not None
            patient.allergy_status = status
            patient.allergies = entries
            repo.update(patient)
            session.commit()
        with _session(engine, tenant, _CLINICIAN_A) as session:
            stored = PostgresPatientRepository(session).get(patient_id, _CLINICIAN_A)
            assert stored is not None
            assert (stored.allergy_status, stored.allergies) == (status, entries)

    with _session(engine, tenant, _CLINICIAN_A) as session, pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE patients SET allergy_status = 'recorded' WHERE id = CAST(:pid AS uuid)"),
            {"pid": patient_id},
        )


# --- The carry-over -------------------------------------------------------------


def _set_rls(conn, schema: str, table: str, *, on: bool) -> None:  # type: ignore[no-untyped-def]
    if on:
        conn.execute(text(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} FORCE ROW LEVEL SECURITY"))
    else:
        conn.execute(text(f"ALTER TABLE {schema}.{table} NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {schema}.{table} DISABLE ROW LEVEL SECURITY"))


_FREE_TEXT = {
    "coded": ("F41.1 Generalized anxiety disorder", "Generalized anxiety disorder", "F41.1"),
    "uncoded": ("Adjustment disorder with anxiety", "Adjustment disorder with anxiety", None),
    "two_codes": ("F41.1, F33.1", "F41.1, F33.1", None),
}


@pytest.fixture
def tenant_at_parent(engine: Engine) -> Iterator[tuple[str, dict[str, str]]]:
    """A tenant one revision back, holding charts with free-text diagnoses."""
    schema = f"practice_test_carry_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        cfg = _alembic_config_for(schema)
        cfg.attributes["connection"] = conn
        cfg.attributes["version_table_schema"] = schema
        command.downgrade(cfg, _PARENT)

    ids: dict[str, str] = {}
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "patients", on=False)
        for name, diagnosis in [*((k, v[0]) for k, v in _FREE_TEXT.items()), ("blank", "  ")]:
            ids[name] = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, diagnosis, created_at, updated_at) "
                    "VALUES (CAST(:id AS uuid), 'A', 'B', 'a', 'b', 'active', 0, :dx, now(), now())"
                ),
                {"id": ids[name], "dx": diagnosis},
            )
        _set_rls(conn, schema, "patients", on=True)

    yield schema, ids
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


def test_each_free_text_diagnosis_becomes_one_active_problem(
    engine: Engine, tenant_at_parent: tuple[str, dict[str, str]]
) -> None:
    schema, ids = tenant_at_parent

    result = upgrade_tenant_schema(engine, schema)

    assert result.status is TenantStatus.SUCCESS, result.detail
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        _set_rls(conn, schema, "patients", on=False)
        _set_rls(conn, schema, "patient_problems", on=False)
        problems = {
            str(row.patient_id): row
            for row in conn.execute(
                text("SELECT patient_id, label, icd10_code, status, added_by FROM patient_problems")
            )
        }
        lines = {
            str(row.id): row.diagnosis
            for row in conn.execute(text("SELECT id, diagnosis FROM patients"))
        }
        _set_rls(conn, schema, "patients", on=True)
        _set_rls(conn, schema, "patient_problems", on=True)

    for name, (_text, label, code) in _FREE_TEXT.items():
        row = problems[ids[name]]
        assert (row.label, row.icd10_code, row.status, row.added_by) == (
            label,
            code,
            "active",
            None,
        )
        assert lines[ids[name]] == (f"{label} ({code})" if code else label)
    assert ids["blank"] not in problems
