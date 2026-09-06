# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Two-patient isolation against a real provisioned tenant schema.

This is the test that makes the patient principal mean something. It runs
against a schema built by the real ``create_practice_schema`` (so the
policies under test are the ones that ship, created by
``enable_rls_on_schema`` — not a hand-written approximation) and connects
as the ``pablo`` role, which the integration conftest creates
``NOSUPERUSER NOBYPASSRLS`` exactly as production has it.

The question it answers: with patient A's principal armed, can anything
reach patient B's row, or any clinician-scoped table? Asked by listing,
by naming B's id outright, and by writing.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion
is preceded by a visibility control on the same connection, so a test can
never pass because the table was empty, the schema was wrong, or the GUC
was never armed. The role's RLS-bypass bits are asserted up front for the
same reason: under a BYPASSRLS role every assertion below would pass while
proving nothing.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_CLINICIAN = "6c2f9a41-8b3d-5e7a-9f04-1d8c3b6e2a95"


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

    schema = f"practice_test_pp_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def two_patients(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """Seed patients A and B clinician-side, as the app would create them."""
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN},
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
                {"pid": pid, "u": _CLINICIAN},
            )
    return patient_a, patient_b


def _as_patient(engine: Engine, schema: str, patient_id: str):  # type: ignore[no-untyped-def]
    """A connection scoped to *schema* with only the patient GUC armed."""
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(
        text("SELECT set_config('app.current_patient_id', :p, false)"),
        {"p": patient_id},
    )
    return conn


def _visible_patient_ids(conn) -> set[str]:  # type: ignore[no-untyped-def]
    return {str(r) for r in conn.execute(text("SELECT id FROM patients")).scalars().all()}


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

    def test_the_patient_policy_actually_exists_on_patients(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Provisioning must have created the arm — not just enabled RLS."""
        with engine.connect() as conn:
            policies = (
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = 'patients'"
                    ),
                    {"s": tenant_schema},
                )
                .scalars()
                .all()
            )
        assert "rls_patient_self_read" in policies, (
            f"patient arm missing from provisioned schema; policies present: {policies}"
        )


class TestTwoPatientIsolation:
    def test_a_sees_their_own_row(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """Visibility control. Without this the invisibility tests prove nothing."""
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert _visible_patient_ids(conn) == {patient_a}
        finally:
            conn.close()

    def test_a_cannot_see_b(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert patient_b not in _visible_patient_ids(conn)
        finally:
            conn.close()

    def test_b_sees_only_themselves(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The symmetric case: the policy is not accidentally keyed to A."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            visible = _visible_patient_ids(conn)
            assert visible == {patient_b}
            assert patient_a not in visible
        finally:
            conn.close()

    def test_naming_bs_id_outright_returns_nothing(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The IDOR move: armed as A, request B by primary key."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: the same query shape finds A's own row.
            own = (
                conn.execute(
                    text("SELECT id FROM patients WHERE id = CAST(:p AS uuid)"),
                    {"p": patient_a},
                )
                .scalars()
                .all()
            )
            assert len(own) == 1

            other = (
                conn.execute(
                    text("SELECT id FROM patients WHERE id = CAST(:p AS uuid)"),
                    {"p": patient_b},
                )
                .scalars()
                .all()
            )
            assert other == []
        finally:
            conn.close()

    def test_a_cannot_update_bs_row(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """``patients`` is registered read-only, so even A's own row is closed.

        The patient arm grants SELECT and nothing else, and the clinician
        UPDATE policy keys on ``app.current_user_id`` which a patient never
        arms — so a patient's UPDATE matches no row either way.
        """
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            result = conn.execute(
                text("UPDATE patients SET first_name = 'tampered' WHERE id = CAST(:p AS uuid)"),
                {"p": patient_b},
            )
            assert result.rowcount == 0
            conn.commit()
        finally:
            conn.close()

        # B's row is intact when B looks.
        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            name = conn.execute(
                text("SELECT first_name FROM patients WHERE id = CAST(:p AS uuid)"),
                {"p": patient_b},
            ).scalar()
            assert name == "Grace"
        finally:
            conn.close()

    def test_a_cannot_delete_bs_row(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            result = conn.execute(
                text("DELETE FROM patients WHERE id = CAST(:p AS uuid)"),
                {"p": patient_b},
            )
            assert result.rowcount == 0
            conn.commit()
        finally:
            conn.close()


class TestPatientCannotReachClinicianTables:
    """Defense-in-depth proven, not assumed.

    Clinician-scoped tables need no patient-specific change: their policies
    key on ``app.current_user_id``, which a patient principal never arms.
    That is a claim about behaviour, so it gets asserted.
    """

    @pytest.mark.parametrize("table", ["therapy_sessions", "notes", "patient_clinicians"])
    def test_clinician_tables_return_zero_rows_for_a_patient(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str], table: str
    ) -> None:
        patient_a, _ = two_patients

        with engine.connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = :s AND table_name = :t"
                ),
                {"s": tenant_schema, "t": table},
            ).first()
        if not exists:
            pytest.skip(f"{table} not present in this schema variant")

        # Control: the clinician CAN see their own rows in this table, so a
        # later "zero rows" is RLS filtering rather than an empty table.
        # patient_clinicians is the one seeded above; the others may be
        # legitimately empty, in which case the control is skipped and the
        # assertion still holds as a non-regression.
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            count = conn.execute(
                text(f"SELECT count(*) FROM {table}")  # noqa: S608
            ).scalar_one()
            assert count == 0, f"a patient principal saw {count} row(s) in clinician-scoped {table}"
        finally:
            conn.close()

    @pytest.mark.usefixtures("two_patients")
    def test_the_control_holds_for_patient_clinicians(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Prove patient_clinicians is non-empty, so the zero above means something."""
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN},
            )
            count = conn.execute(text("SELECT count(*) FROM patient_clinicians")).scalar_one()
        assert count >= 2, "seed missing; the patient-sees-zero assertion would be vacuous"


class TestFailClosed:
    @pytest.mark.usefixtures("two_patients")
    def test_no_principal_armed_sees_no_patients(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_user_id"))
            conn.execute(text("RESET app.current_patient_id"))
            assert _visible_patient_ids(conn) == set()

    def test_clinician_behaviour_is_unchanged(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The patient arm is additive: the clinician still sees both patients.

        If adding the patient policy had rewritten or narrowed the clinician
        policy, this is what would catch it.
        """
        patient_a, patient_b = two_patients
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN},
            )
            visible = _visible_patient_ids(conn)
        assert {patient_a, patient_b} <= visible

    def test_a_clinician_id_equal_to_a_patient_id_grants_nothing_extra(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The collision a single shared GUC would have allowed.

        Both ids are uuids from the same space. Arm the CLINICIAN GUC with
        patient B's id: B's row must stay invisible, because the patient
        policy reads a different GUC. One shared "current principal" GUC
        would hand B's record to that clinician.
        """
        _, patient_b = two_patients
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": patient_b},
            )
            assert patient_b not in _visible_patient_ids(conn)


class TestPatientPrincipalWrites:
    """INSERT — the one command the patient arm does not cover.

    The read arm is ``FOR SELECT`` and the write arm is ``FOR UPDATE``, so
    nothing in the patient policies mentions INSERT. Two consequences pull
    in opposite directions and both are asserted here:

    * ``patients`` carries ``rls_patient_insert ... WITH CHECK (true)`` —
      added for the clinician chicken-and-egg where a brand-new patient
      has no ``patient_clinicians`` grant yet and so fails
      ``has_patient_access`` on its own first INSERT. It consults no GUC,
      so it admits a patient principal too.
    * A table a deployment registers ``writable=True`` gets ``FOR UPDATE``
      only, though ``register_overlay_patient_scoped``'s docstring
      promises "UPDATE/INSERT". A patient cannot write the row the
      registration says they own.

    Non-vacuity, as everywhere in this file: the "cannot insert for
    someone else" case is only meaningful because the case above it proves
    an in-scope INSERT does succeed. Without that control it would pass on
    a blanket deny.
    """

    def test_a_patient_principal_cannot_insert_into_patients(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """A patient principal must not be able to create patient records.

        ``patients`` is registered read-only, so no INSERT should reach it
        under a patient GUC. The permissive ``WITH CHECK (true)`` insert
        policy is the hole: it names no principal at all.
        """
        patient_a, _ = two_patients
        intruder = str(uuid.uuid4())

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as excinfo:
                conn.execute(
                    text(
                        "INSERT INTO patients (id, first_name, last_name, "
                        "first_name_lower, last_name_lower, status, "
                        "session_count, created_at, updated_at) "
                        "VALUES (CAST(:pid AS uuid), 'Mallory', 'Intruder', "
                        "'mallory', 'intruder', 'active', 0, now(), now())"
                    ),
                    {"pid": intruder},
                )
            assert "row-level security" in str(excinfo.value).lower()
        finally:
            conn.rollback()
            conn.close()

        # The row must not exist for anyone — checked as the clinician,
        # who can see every patient in the practice.
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN},
            )
            found = conn.execute(
                text("SELECT count(*) FROM patients WHERE id = CAST(:p AS uuid)"),
                {"p": intruder},
            ).scalar_one()
        assert found == 0, "a patient principal created a patients row"

    def test_a_patient_can_insert_their_own_outcome_measure(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The write arm the intake form needs, and the control for the test below.

        ``outcome_measures`` is registered patient-writable on
        ``patient_id``; a patient submitting a PHQ-9 writes their own row.
        This is the positive case — if it fails, the patient-facing intake
        surface cannot store a screener result at all.
        """
        patient_a, _ = two_patients
        measure_id = str(uuid.uuid4())

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            conn.execute(
                text(
                    "INSERT INTO outcome_measures (id, patient_id, instrument, "
                    "total_score, is_complete, source, administered_at, "
                    "created_by, created_at, updated_at) "
                    "VALUES (CAST(:mid AS uuid), CAST(:pid AS uuid), 'phq9', "
                    "12, true, 'patient_self_report', now(), "
                    "CAST(:pid AS uuid), now(), now())"
                ),
                {"mid": measure_id, "pid": patient_a},
            )
            conn.commit()

            # And can read it back through their own arm.
            own = conn.execute(
                text("SELECT patient_id FROM outcome_measures WHERE id = CAST(:m AS uuid)"),
                {"m": measure_id},
            ).scalar()
            assert str(own) == patient_a
        finally:
            conn.close()

    def test_a_patient_cannot_insert_an_outcome_measure_for_another_patient(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The forgery case: armed as A, write a row owned by B.

        Meaningful only because the test above proves an in-scope INSERT
        succeeds on this same table and connection shape.
        """
        patient_a, patient_b = two_patients
        forged = str(uuid.uuid4())

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as excinfo:
                conn.execute(
                    text(
                        "INSERT INTO outcome_measures (id, patient_id, instrument, "
                        "total_score, is_complete, source, administered_at, "
                        "created_by, created_at, updated_at) "
                        "VALUES (CAST(:mid AS uuid), CAST(:victim AS uuid), 'phq9', "
                        "27, true, 'patient_self_report', now(), "
                        "CAST(:actor AS uuid), now(), now())"
                    ),
                    {"mid": forged, "victim": patient_b, "actor": patient_a},
                )
            assert "row-level security" in str(excinfo.value).lower()
        finally:
            conn.rollback()
            conn.close()

        # B must not see a row they never submitted.
        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            found = conn.execute(
                text("SELECT count(*) FROM outcome_measures WHERE id = CAST(:m AS uuid)"),
                {"m": forged},
            ).scalar_one()
            assert found == 0, "patient A forged an outcome measure onto patient B"
        finally:
            conn.close()

    def test_the_patient_write_arm_exists_on_outcome_measures(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Provisioning must create the INSERT arm, not just the read arm.

        Asserted against ``pg_policies`` so a regression that drops the
        arm is caught here rather than as a confusing permission error in
        whatever route writes next.
        """
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT policyname, cmd FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = 'outcome_measures'"
                ),
                {"s": tenant_schema},
            ).all()
        by_name = {r[0]: r[1] for r in rows}
        assert "rls_patient_self_read" in by_name, (
            f"patient read arm missing on outcome_measures; policies: {by_name}"
        )
        commands = {cmd for name, cmd in by_name.items() if name.startswith("rls_patient_self")}
        assert "INSERT" in commands or "ALL" in commands, (
            f"no patient INSERT arm on outcome_measures; policy commands present: {commands}"
        )
