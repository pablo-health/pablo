# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for coverage on file: provisioning and RLS.

A fresh tenant provisioned from the template must carry both ``payers`` and
``patient_coverage``, and the ``has_patient_access`` policy on
``patient_coverage`` must hide a client's plan from a clinician with no grant
— proven with a real NOSUPERUSER NOBYPASSRLS role (see conftest.py).

Non-vacuous: every "B sees nothing" assertion is preceded by "A sees the
row", so an empty table cannot pass for isolation.

``payers`` is practice-level and deliberately not row-scoped: the same
clinician B who cannot see the coverage can see the payer list, because
the list is the practice's, not a client's.

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

    schema = f"practice_test_cov_rls_{uuid.uuid4().hex[:8]}"
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


def _repos(engine: Engine, tenant_schema: str, user_id: str) -> tuple[Any, Any, Any, Any, Any]:
    """Postgres payer + coverage repositories on a tenant session armed as ``user_id``."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.coverage import (  # noqa: PLC0415
        PostgresPatientCoverageRepository,
        PostgresPayerRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return (
        PostgresPayerRepository(session),
        PostgresPatientCoverageRepository(session),
        session,
        schema_token,
        uid_token,
    )


def _release(session: Any, schema_token: Any, uid_token: Any) -> None:
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)
    _current_user_id.reset(uid_token)


def _coverage(patient_id: str, payer_id: str, member_id: str = "W123") -> Any:
    from app.models.coverage import PatientCoverage  # noqa: PLC0415

    now = datetime.now(UTC)
    return PatientCoverage(
        id=str(uuid.uuid4()),
        patient_id=patient_id,
        payer_id=payer_id,
        member_id=member_id,
        created_at=now,
        updated_at=now,
    )


class TestProvisioning:
    def test_fresh_tenant_has_both_tables(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name IN ('payers', 'patient_coverage')"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert set(rows) == {"payers", "patient_coverage"}

    def test_payers_carries_the_deadline_columns_with_their_defaults(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT column_name, column_default FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = 'payers' "
                    "AND column_name IN "
                    "('timely_filing_days', 'corrected_claim_days', 'appeal_days')"
                ),
                {"s": tenant_schema},
            ).all()
        defaults = dict(rows)
        assert defaults == {
            "timely_filing_days": "90",
            "corrected_claim_days": "90",
            "appeal_days": "180",
        }

    def test_rls_posture(self, engine: Engine, tenant_schema: str) -> None:
        """patient_coverage is force-RLS'd with the patient policy; payers is not."""
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname IN ('payers', 'patient_coverage')"
                ),
                {"s": tenant_schema},
            ).all()
            posture = {name: (rls, forced) for name, rls, forced in rows}
            assert posture["patient_coverage"] == (True, True)
            assert posture["payers"] == (False, False)

            policies = conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = 'patient_coverage'"
                ),
                {"s": tenant_schema},
            ).scalars()
            assert "rls_patient_access" in set(policies)


class TestGranteeCanReadAndWrite:
    def test_add_payer_and_coverage_then_read_back(
        self, engine: Engine, tenant_schema: str, patient_id: str
    ) -> None:
        from app.services.coverage_intake import new_payer  # noqa: PLC0415

        payers, coverage, session, s_tok, u_tok = _repos(engine, tenant_schema, _CLINICIAN_A)
        try:
            payer = payers.create(new_payer(name="Aetna", payer_id="60054"))
            created = coverage.create(_coverage(patient_id, payer.id))
            session.commit()

            active = coverage.get_active(patient_id)
            assert active is not None, "Clinician A must read back the plan they put on file"
            assert active.id == created.id
            assert active.member_id == "W123"
            assert payers.get(payer.id) is not None
        finally:
            _release(session, s_tok, u_tok)

    def test_one_active_coverage_per_client(
        self, engine: Engine, tenant_schema: str, patient_id: str
    ) -> None:
        from app.repositories.coverage import ActiveCoverageExistsError  # noqa: PLC0415

        _, coverage, session, s_tok, u_tok = _repos(engine, tenant_schema, _CLINICIAN_A)
        try:
            active = coverage.get_active(patient_id)
            assert active is not None, "Control: a plan is on file from the previous test"
            with pytest.raises(ActiveCoverageExistsError):
                coverage.create(_coverage(patient_id, active.payer_id, member_id="SECOND"))
            session.rollback()

            # Deactivating the first makes room for a replacement.
            coverage.update(active.model_copy(update={"active": False}))
            replacement = coverage.create(_coverage(patient_id, active.payer_id, "SECOND"))
            session.commit()
            now_active = coverage.get_active(patient_id)
            assert now_active is not None
            assert now_active.id == replacement.id
        finally:
            _release(session, s_tok, u_tok)


class TestNonGranteeIsIsolated:
    def test_clinician_b_sees_no_coverage(
        self, engine: Engine, tenant_schema: str, patient_id: str
    ) -> None:
        # Control: A sees the plan.
        _, coverage_a, session_a, s_a, u_a = _repos(engine, tenant_schema, _CLINICIAN_A)
        try:
            assert coverage_a.get_active(patient_id) is not None
        finally:
            _release(session_a, s_a, u_a)

        # Isolation: B has no grant on this client and reads nothing.
        _, coverage_b, session_b, s_b, u_b = _repos(engine, tenant_schema, _CLINICIAN_B)
        try:
            assert coverage_b.get_active(patient_id) is None
        finally:
            _release(session_b, s_b, u_b)

    @pytest.mark.usefixtures("patient_id")
    def test_clinician_b_raw_count_is_zero(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_A},
            )
            control = conn.execute(text("SELECT count(*) FROM patient_coverage")).scalar_one()
            assert control >= 1, "Control: A must count at least one row"
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_B},
            )
            assert conn.execute(text("SELECT count(*) FROM patient_coverage")).scalar_one() == 0
            conn.rollback()

    def test_clinician_b_insert_rejected_by_rls(
        self, engine: Engine, tenant_schema: str, patient_id: str
    ) -> None:
        from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN_B},
            )
            payer_row_id = conn.execute(text("SELECT id FROM payers LIMIT 1")).scalar_one()
            with pytest.raises(ProgrammingError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO patient_coverage (id, patient_id, payer_id, member_id, "
                        "subscriber_relationship, active, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), CAST(:pid AS uuid), CAST(:payer AS uuid), "
                        "'B-TRIED', 'self', false, now(), now())"
                    ),
                    {"pid": patient_id, "payer": payer_row_id},
                )
            conn.rollback()
        assert "row-level security" in str(exc.value).lower()

    def test_payer_list_is_practice_wide(self, engine: Engine, tenant_schema: str) -> None:
        """B has no grant on any client and still sees the practice's payers."""
        payers_b, _, session_b, s_b, u_b = _repos(engine, tenant_schema, _CLINICIAN_B)
        try:
            names = [payer.name for payer in payers_b.list()]
            assert "Aetna" in names
        finally:
            _release(session_b, s_b, u_b)
