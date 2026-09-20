# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What the intake submit route writes, proven against real Postgres.

The route writes three rows in one transaction under one patient principal:
two ``outcome_measures`` rows and the ``patient_intake_submissions`` row they
were scored from. Those are two differently-governed tables —
``outcome_measures`` is reached by ``has_patient_access`` on the clinician
side and by the patient arm on this one — so the pair is what has to hold,
not either table alone.

``patient_intake_submissions`` has its own isolation proof in
``test_patient_intake_submissions_rls.py``. What is new here is the
``outcome_measures`` write arm, which nothing exercised before a patient
could score themselves, and the transaction that spans both tables.

Runs against a schema built by the real ``create_practice_schema``, as the
``pablo`` role, which the integration conftest creates ``NOSUPERUSER
NOBYPASSRLS`` exactly as production has it. Every invisibility assertion is
preceded by a visibility control on the same connection, so nothing can pass
because a table was empty or a GUC was never armed.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

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

_TREATING_CLINICIAN = "6c2f9a41-8b3d-5e7a-9f04-1d8c3b6e2a95"

# What the route submits: an all-zero PHQ-9 and an all-zero GAD-7.
_PHQ9 = {str(i): 0 for i in range(1, 10)}
_GAD7 = {str(i): 0 for i in range(1, 8)}


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

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_intake_routes_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def two_patients(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """Patients A and B, seeded clinician-side as the app would create them."""
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _TREATING_CLINICIAN},
        )
        for pid, first, last in (
            (patient_a, "Ada", "Lovelace"),
            (patient_b, "Grace", "Hopper"),
        ):
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
    return patient_a, patient_b


def _as_patient(engine: Engine, schema: str, patient_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(
        text("SELECT set_config('app.current_patient_id', :p, false)"),
        {"p": patient_id},
    )
    return conn


def _as_clinician(engine: Engine, schema: str, user_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    conn.execute(
        text("SELECT set_config('app.current_user_id', :u, false)"),
        {"u": user_id},
    )
    return conn


def _unarmed(engine: Engine, schema: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("RESET app.current_patient_id"))
    return conn


def _insert_measure(
    conn: Connection,
    patient_id: str,
    measure_id: str,
    instrument: str,
    item_scores: dict[str, int],
) -> None:
    """One self-reported screener, written the way the route writes it."""
    now = datetime.now(UTC).replace(microsecond=0)
    conn.execute(
        text(
            "INSERT INTO outcome_measures "
            "(id, patient_id, instrument, total_score, item_scores, is_complete, "
            " source, administered_at, created_by, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), :instrument, :total, "
            " CAST(:items AS jsonb), true, 'patient_self_report', :now, "
            " CAST(:pid AS uuid), :now, :now)"
        ),
        {
            "id": measure_id,
            "pid": patient_id,
            "instrument": instrument,
            "total": sum(item_scores.values()),
            "items": json.dumps(item_scores),
            "now": now,
        },
    )


def _insert_submission(conn: Connection, patient_id: str, submission_id: str) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    conn.execute(
        text(
            "INSERT INTO patient_intake_submissions "
            "(id, patient_id, submitted_at, payload, created_by, created_at) "
            "VALUES (:id, CAST(:pid AS uuid), :now, CAST(:payload AS jsonb), :by, :now)"
        ),
        {
            "id": submission_id,
            "pid": patient_id,
            "now": now,
            "payload": json.dumps(
                {
                    "form_version": 1,
                    "name_confirmed": True,
                    "dob_confirmed": True,
                    "corrections": None,
                    "reason_text": "seed",
                    "instruments": {"phq9": _PHQ9, "gad7": _GAD7},
                }
            ),
            "by": patient_id,
        },
    )


def _measure_ids(conn: Connection) -> set[str]:
    return {str(r) for r in conn.execute(text("SELECT id FROM outcome_measures")).scalars().all()}


def _submission_ids(conn: Connection) -> set[str]:
    return set(conn.execute(text("SELECT id FROM patient_intake_submissions")).scalars().all())


@pytest.fixture(scope="module")
def submitted(
    engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
) -> dict[str, dict[str, str]]:
    """One intake submission per patient, each written BY that patient.

    All three rows go in one transaction on a connection with only
    ``app.current_patient_id`` armed — the route's shape exactly. If either
    write arm were missing, FORCE ROW LEVEL SECURITY would refuse the INSERT
    and this fixture would error, rather than quietly leaving empty tables
    for the read assertions to pass against.
    """
    written: dict[str, dict[str, str]] = {}
    for patient_id in two_patients:
        ids = {
            "phq9": str(uuid.uuid4()),
            "gad7": str(uuid.uuid4()),
            "submission": f"intake-routes-{patient_id}",
        }
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            _insert_measure(conn, patient_id, ids["phq9"], "phq9", _PHQ9)
            _insert_measure(conn, patient_id, ids["gad7"], "gad7", _GAD7)
            _insert_submission(conn, patient_id, ids["submission"])
            conn.commit()
        finally:
            conn.close()
        written[patient_id] = ids
    return written


class TestRoleReallyEnforcesRls:
    """If this fails, every isolation assertion in this file is meaningless."""

    def test_connecting_role_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).first()
        assert row is not None
        assert not row[0], "connecting role is a superuser; RLS would be bypassed"
        assert not row[1], "connecting role has BYPASSRLS; RLS would be bypassed"


class TestTheSubmitTransaction:
    def test_all_three_rows_land_under_the_patient_principal(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        submitted: dict[str, dict[str, str]],
    ) -> None:
        """The write control. Without it every refusal below proves nothing."""
        patient_a, _ = two_patients
        ids = submitted[patient_a]

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert _measure_ids(conn) == {ids["phq9"], ids["gad7"]}
            assert _submission_ids(conn) == {ids["submission"]}
        finally:
            conn.close()

    @pytest.mark.usefixtures("submitted")
    def test_the_measures_carry_the_patient_as_the_actor(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
    ) -> None:
        """``created_by`` holds whoever acted, and here that is the patient."""
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            rows = conn.execute(
                text("SELECT created_by, source, is_complete FROM outcome_measures")
            ).all()
            assert len(rows) == 2
            for created_by, source, is_complete in rows:
                assert str(created_by) == patient_a
                assert source == "patient_self_report"
                assert is_complete is True
        finally:
            conn.close()

    def test_a_failed_write_leaves_no_half_submission(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The measures and the submission are one transaction, not three.

        The GAD-7 insert names patient B and is refused; the PHQ-9 that went
        in first must not survive the rollback.
        """
        patient_a, patient_b = two_patients
        orphan = str(uuid.uuid4())

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            _insert_measure(conn, patient_a, orphan, "phq9", _PHQ9)
            with pytest.raises(ProgrammingError) as exc:
                _insert_measure(conn, patient_b, str(uuid.uuid4()), "gad7", _GAD7)
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert orphan not in _measure_ids(conn)
        finally:
            conn.close()


class TestTwoPatientIsolation:
    def test_a_cannot_see_bs_measures(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        submitted: dict[str, dict[str, str]],
    ) -> None:
        patient_a, patient_b = two_patients

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            visible = _measure_ids(conn)
            # Control: A sees their own, so the table is not simply empty.
            assert submitted[patient_a]["phq9"] in visible
            assert submitted[patient_b]["phq9"] not in visible
            assert submitted[patient_b]["gad7"] not in visible

            # The IDOR move: armed as A, name B's row outright.
            named = conn.execute(
                text("SELECT id FROM outcome_measures WHERE id = CAST(:m AS uuid)"),
                {"m": submitted[patient_b]["phq9"]},
            ).scalars()
            assert list(named) == []
        finally:
            conn.close()

    def test_a_cannot_replay_bs_submission(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        submitted: dict[str, dict[str, str]],
    ) -> None:
        """Naming B's submission id while armed as A finds nothing."""
        patient_a, patient_b = two_patients

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            own = conn.execute(
                text("SELECT id FROM patient_intake_submissions WHERE id = :s"),
                {"s": submitted[patient_a]["submission"]},
            ).scalars()
            assert list(own) == [submitted[patient_a]["submission"]]

            other = conn.execute(
                text("SELECT id FROM patient_intake_submissions WHERE id = :s"),
                {"s": submitted[patient_b]["submission"]},
            ).scalars()
            assert list(other) == []
        finally:
            conn.close()

    def test_b_sees_only_their_own(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        submitted: dict[str, dict[str, str]],
    ) -> None:
        """The symmetric case: the policy is not accidentally keyed to A."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            assert _measure_ids(conn) == {
                submitted[patient_b]["phq9"],
                submitted[patient_b]["gad7"],
            }
            assert submitted[patient_a]["phq9"] not in _measure_ids(conn)
        finally:
            conn.close()

    def test_a_cannot_score_a_measure_onto_b(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """WITH CHECK on the insert arm: A cannot file a screener as B."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _insert_measure(conn, patient_b, str(uuid.uuid4()), "phq9", _PHQ9)
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("submitted")
    def test_no_principal_armed_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Fail-closed. The rows exist — the fixture put them there."""
        conn = _unarmed(engine, tenant_schema)
        try:
            assert _measure_ids(conn) == set()
            assert _submission_ids(conn) == set()
        finally:
            conn.close()


class TestTheClinicianSeesTheIntake:
    def test_treating_clinician_reads_the_self_reported_measures(
        self,
        engine: Engine,
        tenant_schema: str,
        submitted: dict[str, dict[str, str]],
    ) -> None:
        """The point of writing them: the chart surface can read them back."""
        expected = {ids["phq9"] for ids in submitted.values()} | {
            ids["gad7"] for ids in submitted.values()
        }
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert _measure_ids(conn) == expected
        finally:
            conn.close()
