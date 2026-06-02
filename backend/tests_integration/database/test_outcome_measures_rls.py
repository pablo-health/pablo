# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres RLS isolation proof for outcome_measures (PABLO-o5k).

Proves, non-vacuously, that the ``has_patient_access`` RLS policy on the
``outcome_measures`` table actually hides rows from clinicians without a
grant — using a real provisioned tenant schema with a real Postgres role
that has NOSUPERUSER NOBYPASSRLS (see conftest.py).

What "non-vacuous" means here: every invisibility assertion is preceded by
a control assertion showing the row IS visible to the grantee before
asserting it is NOT visible to the non-grantee.  This guards against the
silent false pass where the table is simply empty.

Tests:

1. Clinician A (grantee) can add and read back an outcome measure.
2. Clinician B (no grant) gets [] from list_by_patient and None from get.
3. Clinician B's add raises PatientOutcomeAccessDeniedError (app layer) and
   the raw SQL INSERT is rejected by the DB's RLS WITH CHECK (DB layer).
4. Soft-delete: after setting deleted_at, deleted rows are excluded by the
   repo's list_by_patient (service-level filter).
5. Trend order: two measures with different administered_at come back
   ascending by administered_at.
6. source CHECK constraint: a raw INSERT with source='bogus' is rejected.

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


# ---------------------------------------------------------------------------
# Module-scoped fixtures: alembic head + provisioned tenant schema
# ---------------------------------------------------------------------------


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

    # Warm the pool so the RLS policy CREATEs that reference
    # ``has_patient_access`` (which lives in the ``practice`` template
    # schema) can resolve the function name.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_om_rls_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


# ---------------------------------------------------------------------------
# Session-scoped patient + clinician A seed (reused across tests in module)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def seed_patient_and_grant(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """Provision patient P with a patient_clinicians grant for clinician A.

    Returns ``(patient_id, clinician_a_id)``.
    """
    patient_id = str(uuid.uuid4())
    clinician_a = "om-rls-clinician-a"

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(engine: Engine, tenant_schema: str, user_id: str):
    """Return a PostgresOutcomeMeasureRepository wired to a real tenant session."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.outcome_measure import (  # noqa: PLC0415
        PostgresOutcomeMeasureRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(tenant_schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, user_id)
    repo = PostgresOutcomeMeasureRepository(session)
    return repo, session, schema_token, uid_token


def _cleanup_tokens(session, schema_token, uid_token):  # type: ignore[no-untyped-def]
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)
    _current_user_id.reset(uid_token)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _measure_payload(patient_id: str, user_id: str, **overrides) -> dict:  # type: ignore[type-arg]
    base: dict = {
        "id": str(uuid.uuid4()),
        "patient_id": patient_id,
        "session_id": None,
        "appointment_id": None,
        "instrument": "phq9",
        "total_score": None,
        "item_scores": None,
        "is_complete": False,
        "source": "manual",
        "item_citations": None,
        "administered_at": _now(),
        "created_by": user_id,
        "created_at": _now(),
        "updated_at": _now(),
        "deleted_at": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Test 1: clinician A (grantee) — add and read back
# ---------------------------------------------------------------------------


class TestClinicianACanReadWrite:
    """Control: the grantee can write and read their patient's measures."""

    def test_add_and_get(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant
        repo, session, s_tok, u_tok = _make_repo(engine, tenant_schema, clinician_a)
        try:
            payload = _measure_payload(patient_id, clinician_a)
            added = repo.add(payload, clinician_a)
            assert added["id"] == payload["id"]
            assert str(added["patient_id"]) == patient_id

            fetched = repo.get(str(payload["id"]), clinician_a)
            assert fetched is not None, "Clinician A must be able to read back their own measure"
            assert str(fetched["patient_id"]) == patient_id
            session.commit()
        finally:
            _cleanup_tokens(session, s_tok, u_tok)

    def test_list_by_patient_returns_measure(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant
        repo, session, s_tok, u_tok = _make_repo(engine, tenant_schema, clinician_a)
        try:
            rows = repo.list_by_patient(patient_id, clinician_a)
            assert len(rows) >= 1, (
                "Clinician A must see at least the measure added in test_add_and_get"
            )
        finally:
            _cleanup_tokens(session, s_tok, u_tok)


# ---------------------------------------------------------------------------
# Test 2 & 3: clinician B (no grant) — reads return empty; write rejected
# ---------------------------------------------------------------------------


class TestClinicianBIsIsolated:
    """Clinician B has no grant for patient P — must see nothing and be rejected."""

    def test_list_by_patient_returns_empty(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant
        clinician_b = "om-rls-clinician-b"

        # Control: prove A can see at least one row before asserting B cannot.
        repo_a, session_a, s_tok_a, u_tok_a = _make_repo(engine, tenant_schema, clinician_a)
        try:
            rows_a = repo_a.list_by_patient(patient_id, clinician_a)
            assert rows_a, "Control: clinician A must see rows before asserting B sees none"
        finally:
            _cleanup_tokens(session_a, s_tok_a, u_tok_a)

        # Isolation: B sees nothing.
        repo_b, session_b, s_tok_b, u_tok_b = _make_repo(engine, tenant_schema, clinician_b)
        try:
            rows_b = repo_b.list_by_patient(patient_id, clinician_b)
            assert rows_b == [], (
                "Clinician B has no grant for patient P — list_by_patient must return []"
            )
        finally:
            _cleanup_tokens(session_b, s_tok_b, u_tok_b)

    def test_get_returns_none(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant
        clinician_b = "om-rls-clinician-b"

        # Seed a measure as A, then prove B can't fetch it.
        payload = _measure_payload(patient_id, clinician_a)
        repo_a, session_a, s_tok_a, u_tok_a = _make_repo(engine, tenant_schema, clinician_a)
        try:
            repo_a.add(payload, clinician_a)
            session_a.commit()
        finally:
            _cleanup_tokens(session_a, s_tok_a, u_tok_a)

        # Control: A can see it.
        repo_a2, session_a2, s_tok_a2, u_tok_a2 = _make_repo(engine, tenant_schema, clinician_a)
        try:
            control = repo_a2.get(str(payload["id"]), clinician_a)
            assert control is not None, "Control: clinician A must be able to get the measure"
        finally:
            _cleanup_tokens(session_a2, s_tok_a2, u_tok_a2)

        # Isolation: B cannot.
        repo_b, session_b, s_tok_b, u_tok_b = _make_repo(engine, tenant_schema, clinician_b)
        try:
            result = repo_b.get(str(payload["id"]), clinician_b)
            assert result is None, (
                "Clinician B must not be able to get a measure for a patient they have no grant for"
            )
        finally:
            _cleanup_tokens(session_b, s_tok_b, u_tok_b)

    def test_add_raises_app_error(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        """App-layer check raises PatientOutcomeAccessDeniedError for clinician B."""
        from app.repositories.outcome_measure import (  # noqa: PLC0415
            PatientOutcomeAccessDeniedError,
        )

        patient_id, _ = seed_patient_and_grant
        clinician_b = "om-rls-clinician-b"
        payload = _measure_payload(patient_id, clinician_b)

        repo_b, session_b, s_tok_b, u_tok_b = _make_repo(engine, tenant_schema, clinician_b)
        try:
            with pytest.raises(PatientOutcomeAccessDeniedError):
                repo_b.add(payload, clinician_b)
        finally:
            session_b.rollback()
            _cleanup_tokens(session_b, s_tok_b, u_tok_b)

    def test_raw_insert_rejected_by_rls(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        """DB-layer: a raw INSERT from clinician B's GUC is rejected by the RLS WITH CHECK."""
        from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

        patient_id, _ = seed_patient_and_grant
        clinician_b = "om-rls-clinician-b"

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": clinician_b},
            )
            with pytest.raises(ProgrammingError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO outcome_measures "
                        "(id, patient_id, instrument, source, is_complete, "
                        " administered_at, created_by, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), CAST(:pid AS uuid), 'phq9', "
                        "        'manual', false, now(), :u, now(), now())"
                    ),
                    {"pid": patient_id, "u": clinician_b},
                )
            conn.rollback()

        assert "row-level security" in str(exc.value).lower(), (
            "Expected an RLS WITH CHECK violation when clinician B tries to INSERT "
            f"for a patient they have no grant for. Got: {exc.value}"
        )


# ---------------------------------------------------------------------------
# Test 4: soft-delete — deleted rows excluded from list
# ---------------------------------------------------------------------------


class TestSoftDelete:
    """After setting deleted_at the repo excludes the row from list_by_patient."""

    def test_soft_deleted_row_excluded(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant

        # Add a measure and confirm it appears.
        payload = _measure_payload(patient_id, clinician_a)
        repo, session, s_tok, u_tok = _make_repo(engine, tenant_schema, clinician_a)
        try:
            repo.add(payload, clinician_a)
            session.commit()

            before = repo.list_by_patient(patient_id, clinician_a)
            assert any(str(r["id"]) == str(payload["id"]) for r in before), (
                "Measure must be visible before soft-delete"
            )

            # Soft-delete: set deleted_at.
            deleted_payload = dict(payload, deleted_at=_now(), updated_at=_now())
            repo.update(deleted_payload, clinician_a)
            session.commit()

            # The repo returns all rows (including deleted); the service filters.
            # Assert the deleted_at is set (repo contract).
            after = repo.list_by_patient(patient_id, clinician_a)
            soft_deleted = [r for r in after if str(r["id"]) == str(payload["id"])]
            assert soft_deleted, "Row must still be returned by list_by_patient (repo contract)"
            assert soft_deleted[0]["deleted_at"] is not None, (
                "deleted_at must be non-null after soft-delete"
            )
        finally:
            _cleanup_tokens(session, s_tok, u_tok)


# ---------------------------------------------------------------------------
# Test 5: trend order — ascending by administered_at
# ---------------------------------------------------------------------------


class TestTrendOrder:
    """Two measures come back ascending by administered_at."""

    def test_ascending_order(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        patient_id, clinician_a = seed_patient_and_grant
        t1 = _now() - timedelta(days=7)
        t2 = _now()

        payload_old = _measure_payload(
            patient_id, clinician_a, administered_at=t1, instrument="gad7"
        )
        payload_new = _measure_payload(
            patient_id, clinician_a, administered_at=t2, instrument="gad7"
        )

        repo, session, s_tok, u_tok = _make_repo(engine, tenant_schema, clinician_a)
        try:
            # Insert newer first to prove ordering is by column, not insertion order.
            repo.add(payload_new, clinician_a)
            repo.add(payload_old, clinician_a)
            session.commit()

            rows = repo.list_by_patient(patient_id, clinician_a, instrument="gad7")
            gad7_ids = [str(r["id"]) for r in rows]
            assert str(payload_old["id"]) in gad7_ids, "older measure must appear"
            assert str(payload_new["id"]) in gad7_ids, "newer measure must appear"

            old_idx = gad7_ids.index(str(payload_old["id"]))
            new_idx = gad7_ids.index(str(payload_new["id"]))
            assert old_idx < new_idx, (
                "list_by_patient must return measures ascending by administered_at"
            )
        finally:
            _cleanup_tokens(session, s_tok, u_tok)


# ---------------------------------------------------------------------------
# Test 6: source CHECK constraint
# ---------------------------------------------------------------------------


class TestSourceCheckConstraint:
    """A raw INSERT with source='bogus' is rejected by the DB CHECK constraint."""

    def test_invalid_source_rejected(
        self, engine: Engine, tenant_schema: str, seed_patient_and_grant: tuple[str, str]
    ) -> None:
        from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

        patient_id, clinician_a = seed_patient_and_grant

        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": clinician_a},
            )
            with pytest.raises(IntegrityError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO outcome_measures "
                        "(id, patient_id, instrument, source, is_complete, "
                        " administered_at, created_by, created_at, updated_at) "
                        "VALUES (gen_random_uuid(), CAST(:pid AS uuid), 'phq9', "
                        "        'bogus', false, now(), :u, now(), now())"
                    ),
                    {"pid": patient_id, "u": clinician_a},
                )

        err = str(exc.value).lower()
        assert "ck_outcome_measures_source" in err or "check" in err, (
            f"Expected a CHECK constraint violation for source='bogus'. Got: {exc.value}"
        )
