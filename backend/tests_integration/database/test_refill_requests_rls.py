# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for refill requests.

``refill_requests`` is registered patient-readable AND patient-writable, and
``patient_medications`` became patient-readable so a patient can pick what to
ask for. Both grants are proved here against a schema built by the real
``create_practice_schema`` (the policies under test are the ones that ship),
connected as the ``NOSUPERUSER NOBYPASSRLS`` role the integration conftest
creates, as production has it.

**Non-vacuity is enforced.** Every invisibility assertion is preceded by a
visibility control on the same connection, so nothing passes because a table
was empty or a GUC was never armed.

The repository is exercised on the same connections for the two things only
a database can prove: the cross-patient queue reaches exactly the patients a
clinician has a grant on, and a decision is conditional, so a request
answered once refuses a second answer.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_TREATING_CLINICIAN = "5c0e9a41-2b7d-5f18-9d63-1e4a7b20c8f5"
_STRANGER_CLINICIAN = "b27f4d19-8e60-5a3c-9f17-4d2c6e81a0b3"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_refills_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def two_patients(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """Patients A and B, each with one active and one stopped medication.

    Seeded clinician-side, as the app would, by the treating clinician.
    """
    patient_a, patient_b = str(uuid.uuid4()), str(uuid.uuid4())
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _TREATING_CLINICIAN},
        )
        for pid, first, last in ((patient_a, "Ada", "Lovelace"), (patient_b, "Grace", "Hopper")):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, "
                    "first_name_lower, last_name_lower, status, "
                    "session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, :last, "
                    "lower(:first), lower(:last), 'active', 0, now(), now())"
                ),
                {"pid": pid, "first": first, "last": last},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": pid, "u": _TREATING_CLINICIAN},
            )
            for drug, status in (
                (f"{first}-active", "active"),
                (f"{first}-stopped", "discontinued"),
            ):
                conn.execute(
                    text(
                        "INSERT INTO patient_medications (id, patient_id, drug_name, dose, "
                        "status, created_by, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), CAST(:pid AS uuid), :drug, '10 mg', "
                        ":status, CAST(:u AS uuid), :now, :now)"
                    ),
                    {
                        "pid": pid,
                        "drug": drug,
                        "status": status,
                        "u": _TREATING_CLINICIAN,
                        "now": now,
                    },
                )
    return patient_a, patient_b


def _as_patient(engine: Engine, schema: str, patient_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("SELECT set_config('app.current_patient_id', :p, false)"), {"p": patient_id})
    return conn


def _as_clinician(engine: Engine, schema: str, user_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
    return conn


def _unarmed(engine: Engine, schema: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("RESET app.current_patient_id"))
    return conn


def _ask(conn: Connection, patient_id: str, *, minutes_ago: int = 0) -> str:
    request_id = str(uuid.uuid4())
    created = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    conn.execute(
        text(
            "INSERT INTO refill_requests (id, patient_id, medication_text, status, "
            "created_at, updated_at) VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), "
            "'Sertraline 50 mg', 'requested', :at, :at)"
        ),
        {"id": request_id, "pid": patient_id, "at": created},
    )
    return request_id


def _visible(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {table}")).scalars().all()  # noqa: S608 — literal table names only
    return set(rows)


@pytest.fixture(scope="module")
def requests(engine: Engine, tenant_schema: str, two_patients: tuple[str, str]) -> tuple[str, str]:
    """One request per patient, each written BY that patient.

    Written with only the patient GUC armed, so the fixture itself exercises
    the write arm: were it missing, FORCE ROW LEVEL SECURITY would refuse the
    INSERT and this fixture would error instead of leaving an empty table.
    """
    ids: list[str] = []
    for minutes_ago, patient_id in ((30, two_patients[0]), (10, two_patients[1])):
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            ids.append(_ask(conn, patient_id, minutes_ago=minutes_ago))
            conn.commit()
        finally:
            conn.close()
    return ids[0], ids[1]


class TestRoleReallyEnforcesRls:
    def test_connecting_role_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).first()
        assert row is not None
        assert not row[0], "connecting role is a superuser; RLS would be bypassed"
        assert not row[1], "connecting role has BYPASSRLS; RLS would be bypassed"


class TestTableShipped:
    def test_present_with_rls_forced_and_a_policy(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) AS policies "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = 'refill_requests'"
                    ),
                    {"s": tenant_schema},
                )
                .mappings()
                .first()
            )
        assert row is not None, "refill_requests missing from a freshly provisioned tenant"
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["policies"] >= 1

    def test_template_columns_match_the_model(self, engine: Engine, tenant_schema: str) -> None:
        from app.db.models import RefillRequestRow  # noqa: PLC0415

        with engine.connect() as conn:
            columns = set(
                conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = 'refill_requests'"
                    ),
                    {"s": tenant_schema},
                )
                .scalars()
                .all()
            )
        assert columns == {c.name for c in RefillRequestRow.__table__.columns}

    def test_status_is_constrained(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO refill_requests (id, patient_id, medication_text, status, "
                        "created_at, updated_at) VALUES (gen_random_uuid(), CAST(:pid AS uuid), "
                        "'x', 'sent', now(), now())"
                    ),
                    {"pid": two_patients[0]},
                )
        finally:
            conn.rollback()
            conn.close()


class TestPatientPrincipal:
    def test_sees_their_own_requests_and_not_anothers(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        requests: tuple[str, str],
    ) -> None:
        conn = _as_patient(engine, tenant_schema, two_patients[0])
        try:
            visible = _visible(conn, "refill_requests")
        finally:
            conn.close()
        assert requests[0] in visible  # control
        assert requests[1] not in visible

    def test_cannot_write_a_request_for_another_patient(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        conn = _as_patient(engine, tenant_schema, two_patients[0])
        try:
            _ask(conn, two_patients[0])  # control: their own is fine
            with pytest.raises(DBAPIError):
                _ask(conn, two_patients[1])
        finally:
            conn.rollback()
            conn.close()

    def test_reads_their_own_medications_only(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        conn = _as_patient(engine, tenant_schema, two_patients[0])
        try:
            drugs = set(conn.execute(text("SELECT drug_name FROM patient_medications")).scalars())
        finally:
            conn.close()
        assert "Ada-active" in drugs  # control
        assert not any(d.startswith("Grace") for d in drugs)

    def test_repository_offers_only_their_active_medications(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        from app.repositories.postgres.refill_request import (  # noqa: PLC0415
            PostgresRefillRequestRepository,
        )

        conn = _as_patient(engine, tenant_schema, two_patients[0])
        try:
            repo = PostgresRefillRequestRepository(Session(bind=conn))
            options = repo.list_medication_options(two_patients[0])
            stranger_options = repo.list_medication_options(two_patients[1])
        finally:
            conn.close()
        assert [drug for _, drug, _ in options] == ["Ada-active"]
        assert stranger_options == []


class TestClinicianPrincipal:
    def test_treating_clinician_sees_both_and_a_stranger_sees_none(
        self, engine: Engine, tenant_schema: str, requests: tuple[str, str]
    ) -> None:
        treating = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        stranger = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            assert set(requests) <= _visible(treating, "refill_requests")  # control
            assert _visible(stranger, "refill_requests") == set()
        finally:
            treating.close()
            stranger.close()

    def test_unarmed_sees_nothing(
        self, engine: Engine, tenant_schema: str, requests: tuple[str, str]
    ) -> None:
        treating = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        conn = _unarmed(engine, tenant_schema)
        try:
            assert set(requests) <= _visible(treating, "refill_requests")  # control
            assert _visible(conn, "refill_requests") == set()
        finally:
            treating.close()
            conn.close()

    def test_queue_is_oldest_first_and_only_granted_patients(
        self, engine: Engine, tenant_schema: str, requests: tuple[str, str]
    ) -> None:
        from app.repositories.postgres.refill_request import (  # noqa: PLC0415
            PostgresRefillRequestRepository,
        )

        treating = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        stranger = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            queue = PostgresRefillRequestRepository(Session(bind=treating)).list_queue(
                _TREATING_CLINICIAN, "pending"
            )
            stranger_queue = PostgresRefillRequestRepository(Session(bind=stranger)).list_queue(
                _STRANGER_CLINICIAN, "pending"
            )
        finally:
            treating.close()
            stranger.close()
        ids = [e.request.id for e in queue]
        assert ids.index(requests[0]) < ids.index(requests[1])
        assert {e.patient_first_name for e in queue} >= {"Ada", "Grace"}
        assert stranger_queue == []


class TestDecision:
    def test_is_made_once_and_refused_to_a_stranger(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        from app.repositories.postgres.refill_request import (  # noqa: PLC0415
            PostgresRefillRequestRepository,
        )
        from app.repositories.refill_request import (  # noqa: PLC0415
            RefillRequestAccessDeniedError,
            RefillRequestAlreadyDecidedError,
        )

        writer = _as_patient(engine, tenant_schema, two_patients[0])
        try:
            request_id = _ask(writer, two_patients[0])
            writer.commit()
        finally:
            writer.close()

        stranger = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            with pytest.raises(RefillRequestAccessDeniedError):
                PostgresRefillRequestRepository(Session(bind=stranger)).decide(
                    request_id,
                    _STRANGER_CLINICIAN,
                    status="approved",
                    prescriber_note=None,
                    decided_at=datetime.now(UTC),
                )
        finally:
            stranger.rollback()
            stranger.close()

        treating = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            repo = PostgresRefillRequestRepository(Session(bind=treating))
            decided = repo.decide(
                request_id,
                _TREATING_CLINICIAN,
                status="needs_visit",
                prescriber_note="book a follow-up",
                decided_at=datetime.now(UTC),
            )
            assert decided.status == "needs_visit"
            assert decided.decided_by_user_id == _TREATING_CLINICIAN
            with pytest.raises(RefillRequestAlreadyDecidedError):
                repo.decide(
                    request_id,
                    _TREATING_CLINICIAN,
                    status="approved",
                    prescriber_note=None,
                    decided_at=datetime.now(UTC),
                )
            treating.commit()
        finally:
            treating.close()

        reader = _as_patient(engine, tenant_schema, two_patients[0])
        try:
            status = reader.execute(
                text("SELECT status FROM refill_requests WHERE id = CAST(:id AS uuid)"),
                {"id": request_id},
            ).scalar()
        finally:
            reader.close()
        assert status == "needs_visit"


class TestPatientDeletion:
    def test_their_requests_go_with_the_chart(self, engine: Engine, tenant_schema: str) -> None:
        patient_id = str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _TREATING_CLINICIAN},
            )
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), 'Tmp', 'Patient', 'tmp', 'patient', "
                    "'active', 0, now(), now())"
                ),
                {"pid": patient_id},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient_id, "u": _TREATING_CLINICIAN},
            )
            request_id = _ask(conn, patient_id)
            assert request_id in _visible(conn, "refill_requests")  # control
            conn.execute(
                text("DELETE FROM patients WHERE id = CAST(:pid AS uuid)"), {"pid": patient_id}
            )
            assert request_id not in _visible(conn, "refill_requests")
