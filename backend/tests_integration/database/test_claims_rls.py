# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for claims: provisioning, the repository, and RLS.

A fresh tenant provisioned from the template must carry both ``claims`` and
``claim_lines``, both force-RLS'd with the ``has_patient_access`` policy, and
that policy must hide a client's claims from a clinician with no grant —
proven with a real NOSUPERUSER NOBYPASSRLS role (see conftest.py).

Non-vacuous: every "B sees nothing" assertion is preceded by "A sees the
row", so an empty table cannot pass for isolation. Out-of-band counts arm
``app.current_user_id`` explicitly, since an unarmed session reads 0 from
every RLS-forced table and would pass for isolation too.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_CLINICIAN_A = "1a3d0a8e-6f0c-5f7e-9c6b-6c2f5a1c1a01"
_CLINICIAN_B = "2b4e1b9f-7a1d-5a8f-8d7c-7d3a6b2d2b02"
_TABLES = ("claims", "claim_lines")


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_claims_rls_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient_id(engine: Engine, tenant_schema: str) -> str:
    """A client granted to clinician A only."""
    pid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN_A}
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                "last_name_lower, status, session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', 'Patient', 'test', 'patient', "
                "'active', 0, now(), now())"
            ),
            {"pid": pid},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": pid, "u": _CLINICIAN_A},
        )
    return pid


def _session(engine: Engine, tenant_schema: str, user_id: str) -> tuple[Any, Any, Any]:
    """A tenant session armed as ``user_id``, plus the tokens to release it."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return session, schema_token, uid_token


def _release(session: Any, schema_token: Any, uid_token: Any) -> None:
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)
    _current_user_id.reset(uid_token)


def _claim(patient_id: str, coverage_id: str, payer_id: str) -> Any:
    from tests.claims_fixtures import claim, line  # noqa: PLC0415

    claim_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    control = uuid.uuid4().hex[:12].upper()
    return claim(
        id=claim_id,
        control_number=control,
        patient_id=patient_id,
        coverage_id=coverage_id,
        payer_id=payer_id,
        created_at=now,
        updated_at=now,
        lines=[
            line(
                id=str(uuid.uuid4()),
                claim_id=claim_id,
                patient_id=patient_id,
                line_control_number=f"{control}L1",
                created_at=now,
            )
        ],
    )


@pytest.fixture(scope="module")
def coverage_ids(engine: Engine, tenant_schema: str, patient_id: str) -> tuple[str, str]:
    """A payer and an active coverage for the client, as clinician A. (coverage_id, payer_id)"""
    from app.models.coverage import PatientCoverage  # noqa: PLC0415
    from app.repositories.postgres.coverage import (  # noqa: PLC0415
        PostgresPatientCoverageRepository,
        PostgresPayerRepository,
    )
    from app.services.coverage_intake import new_payer  # noqa: PLC0415

    session, s_tok, u_tok = _session(engine, tenant_schema, _CLINICIAN_A)
    try:
        payer = PostgresPayerRepository(session).create(
            new_payer(name="Stedi Test Payer", payer_id="STEDI")
        )
        now = datetime.now(UTC)
        coverage = PostgresPatientCoverageRepository(session).create(
            PatientCoverage(
                id=str(uuid.uuid4()),
                patient_id=patient_id,
                payer_id=payer.id,
                member_id="123456789",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
        return coverage.id, payer.id
    finally:
        _release(session, s_tok, u_tok)


class TestProvisioning:
    def test_fresh_tenant_has_both_tables(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name IN ('claims', 'claim_lines')"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert set(rows) == set(_TABLES)

    def test_both_tables_are_forced_rls_with_the_patient_policy(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname IN ('claims', 'claim_lines')"
                ),
                {"s": tenant_schema},
            ).all()
            posture = {name: (rls, forced) for name, rls, forced in rows}
            assert posture == {"claims": (True, True), "claim_lines": (True, True)}

            for table in _TABLES:
                policies = conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": table},
                ).scalars()
                assert "rls_patient_access" in set(policies), table

    def test_control_number_is_unique_within_the_practice(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            constraints = conn.execute(
                text(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_schema = :s AND table_name = 'claims' "
                    "AND constraint_type = 'UNIQUE'"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert "ux_claims_control_number" in set(constraints)


class TestGranteeCanReadAndWrite:
    def test_create_then_read_back_with_lines(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415

        coverage_id, payer_id = coverage_ids
        session, s_tok, u_tok = _session(engine, tenant_schema, _CLINICIAN_A)
        try:
            repo = PostgresClaimRepository(session)
            created = repo.create(_claim(patient_id, coverage_id, payer_id))
            session.commit()

            read = repo.get(created.id)
            assert read is not None, "Clinician A must read back the claim they built"
            assert read.control_number == created.control_number
            assert read.state == "draft"
            assert read.subscriber_snapshot == created.subscriber_snapshot
            assert read.billing_snapshot == created.billing_snapshot
            assert [line.cpt for line in read.lines] == ["90837"]
            assert read.lines[0].service_date == created.lines[0].service_date
            assert read.lines[0].dx_pointers == [1]
            assert repo.list_by_patient(patient_id)[0].id == created.id
        finally:
            _release(session, s_tok, u_tok)

    def test_update_writes_state_and_line_amounts(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from app.claims.transitions import advance  # noqa: PLC0415
        from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415

        coverage_id, payer_id = coverage_ids
        session, s_tok, u_tok = _session(engine, tenant_schema, _CLINICIAN_A)
        try:
            repo = PostgresClaimRepository(session)
            created = repo.create(_claim(patient_id, coverage_id, payer_id))
            validated = advance(created, "validate", now=datetime.now(UTC))
            validated.lines[0].allowed_cents = 12000
            saved = repo.update(validated)
            session.commit()

            assert saved.state == "validated"
            read = repo.get(created.id)
            assert read is not None
            assert read.state == "validated"
            assert read.lines[0].allowed_cents == 12000
            # The line's charge is fixed at build time and did not move.
            assert read.lines[0].charge_cents == 15000
        finally:
            _release(session, s_tok, u_tok)

    def test_duplicate_control_number_is_refused(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        coverage_id, payer_id = coverage_ids
        session, s_tok, u_tok = _session(engine, tenant_schema, _CLINICIAN_A)
        try:
            repo = PostgresClaimRepository(session)
            first = repo.create(_claim(patient_id, coverage_id, payer_id))
            session.commit()
            twin = _claim(patient_id, coverage_id, payer_id).model_copy(
                update={"control_number": first.control_number}
            )
            with pytest.raises(IntegrityError):
                repo.create(twin)
            session.rollback()
        finally:
            _release(session, s_tok, u_tok)


class TestNonGranteeIsIsolated:
    def test_clinician_b_reads_no_claim(
        self, engine: Engine, tenant_schema: str, patient_id: str
    ) -> None:
        from app.repositories.postgres.claims import PostgresClaimRepository  # noqa: PLC0415

        # Control: A sees at least one claim from the tests above.
        session_a, s_a, u_a = _session(engine, tenant_schema, _CLINICIAN_A)
        try:
            claims_a = PostgresClaimRepository(session_a).list_by_patient(patient_id)
            assert claims_a, "Control: A must see the claims built above"
            claim_id = claims_a[0].id
        finally:
            _release(session_a, s_a, u_a)

        # Isolation: B has no grant on this client and reads nothing.
        session_b, s_b, u_b = _session(engine, tenant_schema, _CLINICIAN_B)
        try:
            repo_b = PostgresClaimRepository(session_b)
            assert repo_b.get(claim_id) is None
            assert repo_b.list_by_patient(patient_id) == []
        finally:
            _release(session_b, s_b, u_b)

    @pytest.mark.usefixtures("patient_id")
    @pytest.mark.parametrize("table", _TABLES)
    def test_clinician_b_raw_count_is_zero(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_A},
            )
            control = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608 — fixed table name from _TABLES
            assert control >= 1, f"Control: A must count at least one {table} row"
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_B},
            )
            assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one() == 0  # noqa: S608 — fixed table name from _TABLES
            conn.rollback()

    def test_clinician_b_insert_rejected_by_rls(
        self, engine: Engine, tenant_schema: str, patient_id: str, coverage_ids: tuple[str, str]
    ) -> None:
        from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

        coverage_id, payer_id = coverage_ids
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_B},
            )
            with pytest.raises(ProgrammingError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO claims (id, control_number, patient_id, coverage_id, "
                        "payer_id, state, frequency_code, total_charge_cents, "
                        "total_paid_cents, diagnosis_codes, billing_snapshot, "
                        "subscriber_snapshot, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), 'BTRIED', CAST(:pid AS uuid), "
                        "CAST(:cov AS uuid), CAST(:payer AS uuid), 'draft', '1', 0, 0, "
                        "'[]', '{}', '{}', now(), now())"
                    ),
                    {"pid": patient_id, "cov": coverage_id, "payer": payer_id},
                )
            conn.rollback()
        assert "row-level security" in str(exc.value).lower()

    def test_clinician_b_cannot_read_lines_through_the_claim(
        self, engine: Engine, tenant_schema: str, patient_id: str
    ) -> None:
        """A line is hidden on its own patient_id, not only because its claim is."""
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_A},
            )
            visible = conn.execute(
                text("SELECT count(*) FROM claim_lines WHERE patient_id = CAST(:pid AS uuid)"),
                {"pid": patient_id},
            ).scalar_one()
            assert visible >= 1
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_B},
            )
            hidden = conn.execute(
                text("SELECT count(*) FROM claim_lines WHERE patient_id = CAST(:pid AS uuid)"),
                {"pid": patient_id},
            ).scalar_one()
            assert hidden == 0
            conn.rollback()
