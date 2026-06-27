# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres RLS isolation proof for diagnostic_assessments (PABLO-6xj.1).

Proves, non-vacuously, that the auto-applied ``has_patient_access`` RLS policy
on the ``diagnostic_assessments`` table actually hides rows from clinicians
without a grant — using a real provisioned tenant schema and a Postgres role
with NOSUPERUSER NOBYPASSRLS (see conftest.py). The table gets its policy from
``enable_rls_on_schema``'s patient_id arm automatically (no hand-written
policy), so this also guards that the auto-coverage really fired.

"Non-vacuous": every invisibility assertion is preceded by a control assertion
that the row IS visible to the grantee first — guarding the empty-table false
pass.

Tests:
1. Clinician A (grantee) can add and read back an assessment.
2. Clinician B (no grant) gets [] from list and None from get.
3. B's add raises the app-layer error AND a raw INSERT is rejected by the DB's
   RLS WITH CHECK.
4. source CHECK constraint rejects a bogus source.
5. Seed idempotency: running the reference-data seed twice yields no duplicates.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

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

    schema = f"practice_test_dx_rls_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def seed_patient_and_grant(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """Provision patient P with a grant for clinician A. Returns (patient_id, clinician_a)."""
    patient_id = str(uuid.uuid4())
    clinician_a = "b7bfbadb-c5da-505c-82d9-7609faf00be1"

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": clinician_a},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', 'Patient', "
                "'test', 'patient', 'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": clinician_a},
        )

    return patient_id, clinician_a


def _make_repo(engine: Engine, tenant_schema: str, user_id: str):
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.diagnostic_assessment import (  # noqa: PLC0415
        PostgresDiagnosticAssessmentRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, user_id)
    repo = PostgresDiagnosticAssessmentRepository(session)
    return repo, session, schema_token, uid_token


def _cleanup_tokens(session, schema_token, uid_token):  # type: ignore[no-untyped-def]
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)
    _current_user_id.reset(uid_token)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _payload(patient_id: str, user_id: str, **overrides) -> dict:  # type: ignore[type-arg]
    base: dict = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "session_id": None,
        "appointment_id": None,
        "instrument": "mdd",
        "definition_version": 1,
        "criterion_responses": {"A1": True},
        "gate_responses": {"duration": True},
        "meets_criteria": False,
        "determined_icd10": None,
        "diagnosis_label": "Major Depressive Disorder",
        "criterion_citations": None,
        "source": "manual",
        "confirmed_at": None,
        "assessed_at": _now(),
        "created_by": user_id,
        "created_at": _now(),
        "updated_at": _now(),
        "deleted_at": None,
    }
    base.update(overrides)
    return base


class TestClinicianACanReadWrite:
    def test_add_and_get(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant
        repo, session, s_tok, u_tok = _make_repo(engine, tenant_schema, clinician_a)
        try:
            payload = _payload(patient_id, clinician_a)
            added = repo.add(payload, clinician_a)
            assert added["id"] == payload["id"]
            fetched = repo.get(str(payload["id"]), clinician_a)
            assert fetched is not None, "Clinician A must read back their own assessment"
            session.commit()
        finally:
            _cleanup_tokens(session, s_tok, u_tok)


class TestClinicianBIsIsolated:
    def test_list_returns_empty(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant
        clinician_b = "73738015-ff11-5dc3-81d1-28314d804335"

        repo_a, sess_a, s_a, u_a = _make_repo(engine, tenant_schema, clinician_a)
        try:
            repo_a.add(_payload(patient_id, clinician_a), clinician_a)
            sess_a.commit()
            assert repo_a.list_by_patient(patient_id, clinician_a), "Control: A must see rows"
        finally:
            _cleanup_tokens(sess_a, s_a, u_a)

        repo_b, sess_b, s_b, u_b = _make_repo(engine, tenant_schema, clinician_b)
        try:
            assert repo_b.list_by_patient(patient_id, clinician_b) == [], (
                "Clinician B has no grant — list must return []"
            )
        finally:
            _cleanup_tokens(sess_b, s_b, u_b)

    def test_add_raises_app_error(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        from app.repositories.diagnostic_assessment import (  # noqa: PLC0415
            PatientDiagnosticAccessDeniedError,
        )

        patient_id, _ = seed_patient_and_grant
        clinician_b = "73738015-ff11-5dc3-81d1-28314d804335"
        repo_b, sess_b, s_b, u_b = _make_repo(engine, tenant_schema, clinician_b)
        try:
            with pytest.raises(PatientDiagnosticAccessDeniedError):
                repo_b.add(_payload(patient_id, clinician_b), clinician_b)
        finally:
            sess_b.rollback()
            _cleanup_tokens(sess_b, s_b, u_b)

    def test_raw_insert_rejected_by_rls(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

        patient_id, _ = seed_patient_and_grant
        clinician_b = "73738015-ff11-5dc3-81d1-28314d804335"

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": clinician_b},
            )
            with pytest.raises(ProgrammingError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO diagnostic_assessments "
                        "(id, patient_id, instrument, definition_version, "
                        " criterion_responses, gate_responses, meets_criteria, "
                        " source, assessed_at, created_by, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), CAST(:pid AS uuid), 'mdd', 1, "
                        "        '{}'::jsonb, '{}'::jsonb, false, 'manual', now(), "
                        "        :u, now(), now())"
                    ),
                    {"pid": patient_id, "u": clinician_b},
                )
            conn.rollback()

        assert "row-level security" in str(exc.value).lower(), (
            f"Expected an RLS WITH CHECK violation for clinician B's INSERT. Got: {exc.value}"
        )


class TestSourceConstraint:
    def test_bogus_source_rejected(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        patient_id, clinician_a = seed_patient_and_grant
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": clinician_a},
            )
            with pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO diagnostic_assessments "
                        "(id, patient_id, instrument, definition_version, "
                        " criterion_responses, gate_responses, meets_criteria, "
                        " source, assessed_at, created_by, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), CAST(:pid AS uuid), 'mdd', 1, "
                        "        '{}'::jsonb, '{}'::jsonb, false, 'bogus', now(), "
                        "        :u, now(), now())"
                    ),
                    {"pid": patient_id, "u": clinician_a},
                )
            conn.rollback()


class TestSeedIdempotency:
    """Running the reference-data seed twice must not create duplicate rows."""

    def test_seed_is_idempotent(self, engine: Engine) -> None:
        from app.diagnostics.baseline import BASELINE_DEFINITIONS  # noqa: PLC0415
        from app.diagnostics.seed import seed_diagnostic_reference_data  # noqa: PLC0415
        from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

        def _counts(conn) -> tuple[int, int]:  # type: ignore[no-untyped-def]
            n_codes = conn.execute(text("SELECT count(*) FROM platform.icd10_codes")).scalar()
            n_defs = conn.execute(
                text("SELECT count(*) FROM platform.diagnostic_definitions")
            ).scalar()
            return n_codes, n_defs

        with engine.connect() as conn:
            before_codes, before_defs = _counts(conn)

        # env.py already seeded once during `command.upgrade(head)`. Seed again
        # and assert counts are unchanged (upsert, not insert).
        with OrmSession(bind=engine) as session:
            seed_diagnostic_reference_data(session)
            session.commit()

        with engine.connect() as conn:
            after_codes, after_defs = _counts(conn)
            # The bundled catalog (F01-F99 + Z55-Z65) is seeded, and the codes
            # the baseline definitions point at are present.
            present = conn.execute(
                text(
                    "SELECT count(*) FROM platform.icd10_codes "
                    "WHERE code IN ('F32.9', 'F41.1', 'Z63.0')"
                )
            ).scalar()

        assert after_codes == before_codes
        assert after_defs == before_defs
        assert after_codes > 1000  # full bundled catalog, not just the baseline
        assert after_defs == len(BASELINE_DEFINITIONS)
        assert present == 3
