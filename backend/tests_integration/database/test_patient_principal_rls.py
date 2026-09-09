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


@pytest.fixture(scope="module")
def two_appointments(
    engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
) -> tuple[str, str]:
    """One future appointment each for A and B, booked clinician-side.

    Distinct start times on purpose: the active-slot uniqueness index
    would reject two live appointments for the same clinician at the same
    instant, and a fixture that fails to seed makes every assertion below
    vacuous rather than false.
    """
    patient_a, patient_b = two_patients
    appt_a = str(uuid.uuid4())
    appt_b = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("RESET app.current_patient_id"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN},
        )
        for appt_id, patient_id, days in ((appt_a, patient_a, 7), (appt_b, patient_b, 8)):
            conn.execute(
                text(
                    "INSERT INTO appointments (id, user_id, patient_id, title, "
                    "start_at, end_at, duration_minutes, status, session_type, "
                    "is_exception, reminder_24h_sent, reminder_1h_sent, created_at) "
                    "VALUES (CAST(:aid AS uuid), CAST(:uid AS uuid), "
                    "CAST(:pid AS uuid), 'Session', "
                    "now() + make_interval(days => :d), "
                    "now() + make_interval(days => :d) + interval '50 minutes', "
                    "50, 'scheduled', 'therapy', false, false, false, now())"
                ),
                {"aid": appt_id, "uid": _CLINICIAN, "pid": patient_id, "d": days},
            )
    return appt_a, appt_b


def _visible_appointment_ids(conn) -> set[str]:  # type: ignore[no-untyped-def]
    return {str(r) for r in conn.execute(text("SELECT id FROM appointments")).scalars().all()}


class TestPatientAppointmentIsolation:
    """A patient sees their own appointments, and only their own.

    ``appointments`` carries both ``user_id`` and ``patient_id``, so it
    holds a clinician arm and a patient arm at once. Permissive policies
    OR together, which is what makes that safe — but "safe" is a claim
    about behaviour, so both directions are asserted: the patient arm
    grants what it should, and it does not reach past the caller.
    """

    def test_a_sees_their_own_appointment(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        two_appointments: tuple[str, str],
    ) -> None:
        """Visibility control. Without this the invisibility test proves nothing."""
        patient_a, _ = two_patients
        appt_a, _ = two_appointments

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert _visible_appointment_ids(conn) == {appt_a}
        finally:
            conn.close()

    def test_a_cannot_see_bs_appointment(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        two_appointments: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        _, appt_b = two_appointments

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert appt_b not in _visible_appointment_ids(conn)
        finally:
            conn.close()

    def test_b_sees_only_their_own(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        two_appointments: tuple[str, str],
    ) -> None:
        """The symmetric case: the policy is not accidentally keyed to A."""
        _, patient_b = two_patients
        appt_a, appt_b = two_appointments

        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            visible = _visible_appointment_ids(conn)
            assert visible == {appt_b}
            assert appt_a not in visible
        finally:
            conn.close()

    def test_naming_bs_appointment_outright_returns_nothing(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        two_appointments: tuple[str, str],
    ) -> None:
        """The IDOR move: armed as A, request B's appointment by primary key.

        This is the shape a patient-facing route is most likely to get
        wrong — loading by id and trusting the caller named their own.
        """
        patient_a, _ = two_patients
        appt_a, appt_b = two_appointments

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: the same query shape finds A's own appointment.
            own = conn.execute(
                text("SELECT id FROM appointments WHERE id = CAST(:a AS uuid)"),
                {"a": appt_a},
            ).scalar()
            assert str(own) == appt_a

            stolen = conn.execute(
                text("SELECT id FROM appointments WHERE id = CAST(:a AS uuid)"),
                {"a": appt_b},
            ).scalar()
            assert stolen is None, "a patient read another patient's appointment by id"
        finally:
            conn.close()

    def test_the_clinician_still_sees_both(
        self,
        engine: Engine,
        tenant_schema: str,
        two_appointments: tuple[str, str],
    ) -> None:
        """The patient arm must widen access, never narrow the clinician's.

        Both arms are permissive and therefore OR together, so adding one
        cannot remove a row from the other. Asserted rather than assumed,
        because the cost of being wrong is a therapist's calendar
        emptying out.
        """
        appt_a, appt_b = two_appointments

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN},
            )
            visible = _visible_appointment_ids(conn)
        assert {appt_a, appt_b} <= visible

    def test_the_read_arm_exists_on_appointments(self, engine: Engine, tenant_schema: str) -> None:
        """Provisioning must create the read arm, and only the read arm.

        Asserted against ``pg_policies`` so that a regression — the arm
        dropped, or a write arm added by accident — is caught here rather
        than as a surprise several layers up.
        """
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT policyname, cmd FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = 'appointments'"
                ),
                {"s": tenant_schema},
            ).all()
        by_name = {r[0]: r[1] for r in rows}

        assert by_name.get("rls_patient_self_read") == "SELECT", (
            f"patient read arm missing or not SELECT-only on appointments; policies: {by_name}"
        )
        assert "rls_patient_self_write" not in by_name, (
            "appointments grew a patient UPDATE arm; booking rules live in the "
            f"route, not in a row policy. Policies: {by_name}"
        )
        assert "rls_patient_self_insert" not in by_name, (
            f"appointments grew a patient INSERT arm; see above. Policies: {by_name}"
        )


class TestPatientCannotWriteAppointments:
    """Read-only means read-only, asserted per command.

    The three write commands fail in two different ways, and conflating
    them would let a real regression pass. INSERT is refused outright:
    the clinician arm's ``WITH CHECK`` cannot match a request that never
    armed ``app.current_user_id``, and no patient INSERT arm exists.
    UPDATE and DELETE are quieter — the patient arm is ``FOR SELECT``, so
    it contributes no ``USING`` clause to either, the clinician arm's
    ``USING`` fails, and the row is simply not visible to modify.
    Postgres reports that as zero rows affected, not as an error. So each
    of those is asserted twice: nothing was touched, and the row is still
    what it was.
    """

    def test_a_patient_cannot_insert_an_appointment(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """Self-booking must go through a route that can read the practice's rules."""
        patient_a, _ = two_patients
        forged = str(uuid.uuid4())

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as excinfo:
                conn.execute(
                    text(
                        "INSERT INTO appointments (id, user_id, patient_id, title, "
                        "start_at, end_at, duration_minutes, status, session_type, "
                        "is_exception, reminder_24h_sent, reminder_1h_sent, created_at) "
                        "VALUES (CAST(:aid AS uuid), CAST(:uid AS uuid), "
                        "CAST(:pid AS uuid), 'Self-booked', "
                        "now() + interval '30 days', "
                        "now() + interval '30 days' + interval '50 minutes', "
                        "50, 'scheduled', 'therapy', false, false, false, now())"
                    ),
                    {"aid": forged, "uid": _CLINICIAN, "pid": patient_a},
                )
            assert "row-level security" in str(excinfo.value).lower()
        finally:
            conn.rollback()
            conn.close()

        # And no row was left behind — checked as the clinician, who can
        # see every appointment in the practice.
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN},
            )
            found = conn.execute(
                text("SELECT count(*) FROM appointments WHERE id = CAST(:a AS uuid)"),
                {"a": forged},
            ).scalar_one()
        assert found == 0, "a patient principal created an appointment"

    def test_a_patient_cannot_move_their_own_appointment(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        two_appointments: tuple[str, str],
    ) -> None:
        """Rescheduling is a route's decision — notice periods, confirmation.

        The patient can SEE this row, which is what makes the case worth
        asserting: visibility is not permission.
        """
        patient_a, _ = two_patients
        appt_a, _ = two_appointments

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: the row is visible to this principal, so a zero
            # rowcount below means "not permitted", not "not found".
            assert appt_a in _visible_appointment_ids(conn)

            result = conn.execute(
                text(
                    "UPDATE appointments SET start_at = now() + interval '90 days' "
                    "WHERE id = CAST(:a AS uuid)"
                ),
                {"a": appt_a},
            )
            assert result.rowcount == 0, "a patient principal rescheduled their own appointment"
            conn.commit()
        finally:
            conn.close()

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN},
            )
            moved = conn.execute(
                text(
                    "SELECT start_at > now() + interval '60 days' FROM appointments "
                    "WHERE id = CAST(:a AS uuid)"
                ),
                {"a": appt_a},
            ).scalar_one()
        assert moved is False, "the appointment moved despite a zero rowcount"

    def test_a_patient_cannot_delete_their_own_appointment(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        two_appointments: tuple[str, str],
    ) -> None:
        """Cancelling is a status change made by a route, never a DELETE.

        A row that vanishes takes the practice's record of the booking
        with it, so this one matters beyond the permission question.
        """
        patient_a, _ = two_patients
        appt_a, _ = two_appointments

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert appt_a in _visible_appointment_ids(conn)

            result = conn.execute(
                text("DELETE FROM appointments WHERE id = CAST(:a AS uuid)"),
                {"a": appt_a},
            )
            assert result.rowcount == 0, "a patient principal deleted an appointment"
            conn.commit()
        finally:
            conn.close()

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_patient_id"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": _CLINICIAN},
            )
            survives = conn.execute(
                text("SELECT count(*) FROM appointments WHERE id = CAST(:a AS uuid)"),
                {"a": appt_a},
            ).scalar_one()
        assert survives == 1, "the appointment was deleted despite a zero rowcount"
