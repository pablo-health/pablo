# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for intake assignments and saved answers.

Both tables are registered patient-readable AND patient-writable: a patient
reads the form they were asked for, saves their answers into it, and reads
them back between sittings. That is a wide grant, so the policies it
produces are proved here against a schema built by the real
``create_practice_schema`` (so the policies under test are the ones that
ship) and connected as the ``pablo`` role, which the integration conftest
creates ``NOSUPERUSER NOBYPASSRLS`` exactly as production has it.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion is
preceded by a visibility control on the same connection, so no assertion
can pass because the table was empty, the schema was wrong or the GUC was
never armed. The role's RLS-bypass bits are asserted up front for the same
reason.

Three things beyond isolation are proved here because only a real database
can prove them: the composite foreign key that keeps
``patient_intake_responses.patient_id`` in step with its assignment's
owner, and the two partial unique indexes that carry the rules of the
feature — one live request per patient per version, one live draft per
question per request.

The last section compares the two producers of these tables. A freshly
provisioned tenant gets them from ``tenant_template.sql``; an existing one
gets them from the alembic revision. Those are different code paths, and
the classic failure is that one ships and the other does not — which
surfaces far from the cause, as ``relation ... does not exist`` in whatever
runs next.

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
from sqlalchemy.exc import IntegrityError, ProgrammingError

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

_ASSIGNMENTS = "patient_intake_assignments"
_RESPONSES = "patient_intake_responses"

# The clinician who created both patients, and so holds a
# ``patient_clinicians`` grant on each.
_TREATING_CLINICIAN = "5b19c704-2e8a-5d31-b4f6-90c27ae1d385"
# A clinician in the same practice with no grant on either patient.
_STRANGER_CLINICIAN = "8d03f261-47b9-5a0c-92e1-3f6b8c05d417"

# The revision this one follows. Rolling a schema back to it and forward
# again replays exactly the revision under test.
_PARENT_REVISION = "d3b71f0c85a4"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _new_schema(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_{label}_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    return schema


def _drop_schema(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    schema = _new_schema(engine, "intake_assign")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def two_patients(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """Seed patients A and B clinician-side, as the app would create them."""
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


@pytest.fixture(scope="module")
def published_form(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """A published version with one question on it, seeded clinician-side.

    A form is practice-level, so it is written on a clinician connection —
    these three tables carry no ``patient_id`` and are scoped by the tenant
    schema alone.
    """
    version_id = str(uuid.uuid4())
    item_id = str(uuid.uuid4())
    template_id = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _TREATING_CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO intake_packet_templates (id, name, created_by, created_at) "
                "VALUES (CAST(:tid AS uuid), 'Intake', CAST(:u AS uuid), now())"
            ),
            {"tid": template_id, "u": _TREATING_CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO intake_packet_versions "
                "(id, template_id, version, published_at, published_by, created_at) "
                "VALUES (CAST(:vid AS uuid), CAST(:tid AS uuid), 1, now(), "
                "CAST(:u AS uuid), now())"
            ),
            {"vid": version_id, "tid": template_id, "u": _TREATING_CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO intake_item_definitions "
                "(id, version_id, key, position, item_type, required, config, "
                "resign_on_new_version) "
                "VALUES (CAST(:iid AS uuid), CAST(:vid AS uuid), 'reason', 0, "
                "'reason', true, '{}'::jsonb, false)"
            ),
            {"iid": item_id, "vid": version_id},
        )
    return version_id, item_id


@pytest.fixture(scope="module")
def spare_item(engine: Engine, tenant_schema: str, published_form: tuple[str, str]) -> str:
    """A second question on the same form, for tests that need a free slot.

    The live-draft index allows one answer per question per assignment, so
    a test that wants to write a control answer onto an assignment the
    fixtures have already answered needs a question they did not use.
    """
    version_id, _ = published_form
    item_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _TREATING_CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO intake_item_definitions "
                "(id, version_id, key, position, item_type, required, config, "
                "resign_on_new_version) "
                "VALUES (CAST(:iid AS uuid), CAST(:vid AS uuid), 'note', 1, "
                "'free_text', false, '{\"max_len\": 50}'::jsonb, false)"
            ),
            {"iid": item_id, "vid": version_id},
        )
    return item_id


@pytest.fixture(scope="module")
def assignments(
    engine: Engine,
    tenant_schema: str,
    two_patients: tuple[str, str],
    published_form: tuple[str, str],
) -> tuple[str, str]:
    """One assignment per patient, sent by the treating clinician."""
    version_id, _ = published_form
    ids: list[str] = []
    conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
    try:
        for patient_id in two_patients:
            assignment_id = str(uuid.uuid4())
            _assign(conn, patient_id, version_id, assignment_id)
            ids.append(assignment_id)
        conn.commit()
    finally:
        conn.close()
    return ids[0], ids[1]


@pytest.fixture(scope="module")
def drafts(
    engine: Engine,
    tenant_schema: str,
    two_patients: tuple[str, str],
    published_form: tuple[str, str],
    assignments: tuple[str, str],
) -> tuple[str, str]:
    """One saved answer per patient, written BY that patient.

    Deliberately not seeded clinician-side. The inserts run on a connection
    with only ``app.current_patient_id`` armed, so they exercise the write
    arm the registration grants — if that arm were missing, FORCE ROW LEVEL
    SECURITY would refuse the INSERT and this fixture would error rather
    than quietly leaving empty tables for the read tests to pass against.
    """
    _, item_id = published_form
    ids: list[str] = []
    for patient_id, assignment_id in zip(two_patients, assignments, strict=True):
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            ids.append(_save(conn, patient_id, assignment_id, item_id))
            conn.commit()
        finally:
            conn.close()
    return ids[0], ids[1]


# ---------------------------------------------------------------------------
# Connections and writes
# ---------------------------------------------------------------------------


def _as_patient(engine: Engine, schema: str, patient_id: str) -> Connection:
    """A connection scoped to *schema* with only the patient GUC armed."""
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(
        text("SELECT set_config('app.current_patient_id', :p, false)"),
        {"p": patient_id},
    )
    return conn


def _as_clinician(engine: Engine, schema: str, user_id: str) -> Connection:
    """A connection scoped to *schema* with only the clinician GUC armed."""
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    conn.execute(
        text("SELECT set_config('app.current_user_id', :u, false)"),
        {"u": user_id},
    )
    return conn


def _unarmed(engine: Engine, schema: str) -> Connection:
    """A connection with no principal armed at all."""
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("RESET app.current_patient_id"))
    return conn


def _assign(
    conn: Connection,
    patient_id: str,
    version_id: str,
    assignment_id: str,
    *,
    status: str = "assigned",
) -> str:
    conn.execute(
        text(
            f"INSERT INTO {_ASSIGNMENTS} "  # noqa: S608 — module constant, no caller input
            "(id, patient_id, version_id, status, assigned_by, assigned_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:vid AS uuid), "
            ":status, CAST(:u AS uuid), :now, :now)"
        ),
        {
            "id": assignment_id,
            "pid": patient_id,
            "vid": version_id,
            "status": status,
            "u": _TREATING_CLINICIAN,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return assignment_id


def _save(
    conn: Connection,
    patient_id: str,
    assignment_id: str,
    item_id: str,
    *,
    response_id: str | None = None,
) -> str:
    response_id = response_id or str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {_RESPONSES} "  # noqa: S608 — module constant, no caller input
            "(id, assignment_id, patient_id, item_id, value, draft, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:aid AS uuid), CAST(:pid AS uuid), "
            "CAST(:iid AS uuid), CAST(:value AS jsonb), true, :now, :now)"
        ),
        {
            "id": response_id,
            "aid": assignment_id,
            "pid": patient_id,
            "iid": item_id,
            "value": '{"text": "seed answer"}',
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return response_id


def _visible_assignments(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_ASSIGNMENTS}")).scalars().all()  # noqa: S608
    return set(rows)


def _visible_responses(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_RESPONSES}")).scalars().all()  # noqa: S608
    return set(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


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


class TestTablesShipped:
    """The migration and the template regen both landed."""

    @pytest.mark.parametrize("table", [_ASSIGNMENTS, _RESPONSES])
    def test_table_present_with_rls_forced_and_a_policy(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity, "
                        "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
                        "AS policy_count "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = :t"
                    ),
                    {"s": tenant_schema, "t": table},
                )
                .mappings()
                .first()
            )
        assert row is not None, (
            f"{table} missing from a freshly-provisioned tenant — the migration "
            "or the tenant_template.sql regen did not ship"
        )
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["policy_count"] >= 1, "RLS forced with no policy is a silent deny-all"

    @pytest.mark.parametrize("table", [_ASSIGNMENTS, _RESPONSES])
    def test_all_four_policy_arms_are_present(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        """Read, insert and update for the patient; access for the clinician."""
        with engine.connect() as conn:
            by_name = dict(
                conn.execute(
                    text(
                        "SELECT policyname, cmd FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": table},
                ).all()
            )
        assert by_name.get("rls_patient_self_read") == "SELECT", by_name
        assert by_name.get("rls_patient_self_insert") == "INSERT", by_name
        assert by_name.get("rls_patient_self_write") == "UPDATE", by_name
        assert "rls_patient_access" in by_name, by_name

    def test_responses_needed_no_bespoke_policy(self, engine: Engine, tenant_schema: str) -> None:
        """The denormalized ``patient_id`` is what avoids one.

        ``chat_messages`` has a hand-written parent-join policy precisely
        because it lacks the column. If this table ever grew one, the
        registration and the models would have quietly diverged.
        """
        with engine.connect() as conn:
            names = set(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": _RESPONSES},
                )
                .scalars()
                .all()
            )
        assert names == {
            "rls_patient_self_read",
            "rls_patient_self_insert",
            "rls_patient_self_write",
            "rls_patient_access",
        }, names


class TestPatientPrincipalIsolation:
    def test_each_patient_reads_their_own_form_and_answer(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
        drafts: tuple[str, str],
    ) -> None:
        """The control. Without it the refusals below prove nothing.

        The answers were inserted by the fixture under each patient's own
        armed GUC and nothing else — so reaching this assertion already
        means the insert arm admitted a patient writing their own row.
        """
        for patient_id, assignment_id, response_id in zip(
            two_patients, assignments, drafts, strict=True
        ):
            conn = _as_patient(engine, tenant_schema, patient_id)
            try:
                assert _visible_assignments(conn) == {assignment_id}
                assert _visible_responses(conn) == {response_id}
            finally:
                conn.close()

    def test_a_patient_can_change_their_own_saved_answer(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        drafts: tuple[str, str],
    ) -> None:
        """The UPDATE arm, which saving over a draft depends on."""
        patient_a, _ = two_patients
        response_a, _ = drafts
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            updated = conn.execute(
                text(
                    f"UPDATE {_RESPONSES} SET value = CAST(:v AS jsonb), "  # noqa: S608
                    "updated_at = now() WHERE id = CAST(:id AS uuid)"
                ),
                {"v": '{"text": "changed my mind"}', "id": response_a},
            ).rowcount
            assert updated == 1
            conn.rollback()
        finally:
            conn.close()

    def test_a_patient_can_advance_their_own_assignment(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """The first save moves the form to ``in_progress`` from the portal."""
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            updated = conn.execute(
                text(
                    f"UPDATE {_ASSIGNMENTS} SET status = 'in_progress', "  # noqa: S608
                    "updated_at = now() WHERE id = CAST(:id AS uuid)"
                ),
                {"id": assignment_a},
            ).rowcount
            assert updated == 1
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_save_an_answer_owned_by_b(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """WITH CHECK on the insert arm: A cannot file an answer as B."""
        patient_a, patient_b = two_patients
        _, assignment_b = assignments
        _, item_id = published_form
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _save(conn, patient_b, assignment_b, item_id)
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_send_themselves_a_form_as_b(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        _, patient_b = two_patients
        version_id, _ = published_form
        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _assign(conn, str(uuid.uuid4()), version_id, str(uuid.uuid4()))
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_see_bs_form_or_answers(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
        drafts: tuple[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        assignment_a, assignment_b = assignments
        _, response_b = drafts

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: A sees their own, so the tables are not simply empty.
            assert assignment_a in _visible_assignments(conn)
            assert assignment_b not in _visible_assignments(conn)
            assert response_b not in _visible_responses(conn)

            # The IDOR move: armed as A, name B's rows outright.
            named = conn.execute(
                text(
                    f"SELECT id FROM {_RESPONSES} "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).scalars()
            assert list(named) == []
        finally:
            conn.close()

    @pytest.mark.usefixtures("drafts")
    def test_a_cannot_change_bs_answer(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
    ) -> None:
        """Invisible to a write as well as to a read: rowcount 0, no error."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: A's own update lands.
            own = conn.execute(
                text(
                    f"UPDATE {_RESPONSES} SET updated_at = now() "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_a},
            ).rowcount
            assert own >= 1
            foreign = conn.execute(
                text(
                    f"UPDATE {_RESPONSES} SET updated_at = now() "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).rowcount
            assert foreign == 0
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("drafts")
    def test_no_principal_armed_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Fail-closed. The rows exist — the fixtures put them there."""
        conn = _unarmed(engine, tenant_schema)
        try:
            assert _visible_assignments(conn) == set()
            assert _visible_responses(conn) == set()
        finally:
            conn.close()


class TestClinicianAccess:
    @pytest.mark.usefixtures("drafts")
    def test_treating_clinician_sees_both_patients(
        self,
        engine: Engine,
        tenant_schema: str,
        assignments: tuple[str, str],
    ) -> None:
        """``has_patient_access`` reaches the rows a grant covers."""
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert set(assignments) <= _visible_assignments(conn)
            assert len(_visible_responses(conn)) >= 2
        finally:
            conn.close()

    @pytest.mark.usefixtures("drafts")
    def test_stranger_clinician_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Same practice, no grant on either patient. The control is above."""
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            assert _visible_assignments(conn) == set()
            assert _visible_responses(conn) == set()
        finally:
            conn.close()

    def test_stranger_clinician_cannot_send_a_form(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        version_id, _ = published_form
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _assign(conn, patient_a, version_id, str(uuid.uuid4()))
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()


class TestIntegrityConstraints:
    """What the database refuses regardless of who is asking."""

    def test_an_answer_cannot_claim_a_patient_its_assignment_does_not_have(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        spare_item: str,
        assignments: tuple[str, str],
    ) -> None:
        """The composite FK: the denormalized column cannot drift.

        Run as the treating clinician, who has a grant on BOTH patients —
        so row security admits the write and the only thing left to refuse
        it is the foreign key.
        """
        patient_a, patient_b = two_patients
        assignment_a, _ = assignments
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # Control: the same insert with the matching patient succeeds.
            # Undone through a savepoint rather than a rollback, because a
            # rollback would also undo this connection's ``search_path``
            # and the refusal below would be a missing table instead.
            savepoint = conn.begin_nested()
            _save(conn, patient_a, assignment_a, spare_item)
            savepoint.rollback()

            with pytest.raises(IntegrityError):
                _save(conn, patient_b, assignment_a, spare_item)
            conn.rollback()
        finally:
            conn.close()

    def test_one_live_assignment_per_patient_per_version(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        """The partial unique index: a second click is not a second form."""
        patient_a, _ = two_patients
        version_id, _ = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            with pytest.raises(IntegrityError) as exc:
                _assign(conn, patient_a, version_id, str(uuid.uuid4()))
            assert "uq_patient_intake_assignments_active" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_a_withdrawn_assignment_leaves_room_for_another(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        """``withdrawn`` is outside the index, so asking again is allowed."""
        patient_a, _ = two_patients
        version_id, _ = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            conn.execute(
                text(
                    f"UPDATE {_ASSIGNMENTS} SET status = 'withdrawn', "  # noqa: S608
                    "withdrawn_at = now() WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_a},
            )
            _assign(conn, patient_a, version_id, str(uuid.uuid4()))
            conn.rollback()
        finally:
            conn.close()

    def test_one_live_draft_per_question(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """The other partial index: saving an answer is an upsert, not an append."""
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        _, item_id = published_form
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(IntegrityError) as exc:
                _save(conn, patient_a, assignment_a, item_id)
            assert "uq_patient_intake_responses_live_draft" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_an_unknown_status_is_refused(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        _, patient_b = two_patients
        version_id, _ = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            with pytest.raises(IntegrityError) as exc:
                _assign(conn, patient_b, version_id, str(uuid.uuid4()), status="posted")
            assert "ck_patient_intake_assignments_status" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_deleting_an_assignment_takes_its_answers(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        """ON DELETE CASCADE, so a removed request leaves no orphan answers."""
        patient_a, _ = two_patients
        version_id, item_id = published_form
        assignment_id = str(uuid.uuid4())
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # A second version, so the live-assignment index is not in play.
            other_version = _second_version(conn, version_id)
            _assign(conn, patient_a, other_version, assignment_id)
            response_id = _save(conn, patient_a, assignment_id, item_id)
            assert response_id in _visible_responses(conn)
            conn.execute(
                text(f"DELETE FROM {_ASSIGNMENTS} WHERE id = CAST(:id AS uuid)"),  # noqa: S608
                {"id": assignment_id},
            )
            assert response_id not in _visible_responses(conn)
            conn.rollback()
        finally:
            conn.close()


def _second_version(conn: Connection, version_id: str) -> str:
    """Another published version of the same form, for a second assignment."""
    new_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO intake_packet_versions "
            "(id, template_id, version, published_at, published_by, created_at) "
            "SELECT CAST(:new AS uuid), template_id, version + 1, now(), "
            "published_by, now() FROM intake_packet_versions "
            "WHERE id = CAST(:old AS uuid)"
        ),
        {"new": new_id, "old": version_id},
    )
    return new_id


def _shape(engine: Engine, schema: str, table: str) -> dict[str, list[str]]:
    """Columns, indexes, constraints and policy names for *table* in *schema*."""
    with engine.connect() as conn:
        columns = list(
            conn.execute(
                text(
                    "SELECT column_name || ' ' || data_type || ' ' || is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t "
                    "ORDER BY column_name"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
        indexes = list(
            conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = :s AND tablename = :t ORDER BY indexname"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
        constraints = list(
            conn.execute(
                text(
                    "SELECT con.conname || ' ' || con.contype::text "
                    "FROM pg_constraint con "
                    "JOIN pg_class c ON c.oid = con.conrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = :t "
                    "ORDER BY con.conname"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
        policies = list(
            conn.execute(
                text(
                    "SELECT policyname FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = :t ORDER BY policyname"
                ),
                {"s": schema, "t": table},
            )
            .scalars()
            .all()
        )
    return {
        "columns": columns,
        "indexes": indexes,
        "constraints": constraints,
        "policies": policies,
    }


def _rebuild_through_the_chain(engine: Engine, schema: str) -> None:
    """Drop both tables, roll the schema back one revision, migrate forward.

    This is how an existing tenant gets them: not from the template but
    from the revision, replayed here over a schema that does not have them.
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_RESPONSES} CASCADE"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_ASSIGNMENTS} CASCADE"))
        conn.execute(
            text(f"UPDATE {schema}.alembic_version SET version_num = :r"),  # noqa: S608
            {"r": _PARENT_REVISION},
        )

    result = upgrade_tenant_schema(engine, schema)
    assert result.status.value == "success", result.detail


def _reapply_policies(engine: Engine, schema: str) -> None:
    """Re-run the provisioning RLS step, as ``create_practice_schema`` does."""
    from app.db import PLATFORM_SCHEMA, enable_rls_on_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(engine) as session:
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        enable_rls_on_schema(session, schema)


@pytest.fixture(scope="module")
def fresh_schema(engine: Engine) -> Iterator[str]:
    """A tenant built the way provisioning builds one: from the template."""
    schema = _new_schema(engine, "intake_assign_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant that got the tables from the revision instead."""
    schema = _new_schema(engine, "intake_assign_migrated")
    _rebuild_through_the_chain(engine, schema)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers of these tables must land the same shape.

    If they drift, the symptom appears wherever a fresh tenant is next
    provisioned, not here — so they are compared directly.
    """

    @pytest.mark.parametrize("table", [_ASSIGNMENTS, _RESPONSES])
    def test_template_and_chain_produce_the_same_table(
        self, engine: Engine, fresh_schema: str, migrated_schema: str, table: str
    ) -> None:
        fresh = _shape(engine, fresh_schema, table)
        migrated = _shape(engine, migrated_schema, table)
        assert fresh["columns"], f"{table} absent from the template-built schema"
        assert migrated["columns"], f"{table} absent from the chain-built schema"
        assert fresh == migrated

    def test_the_revision_is_idempotent(self, engine: Engine, migrated_schema: str) -> None:
        """Fanned out once per practice schema, so replaying it must be a no-op."""
        before = _shape(engine, migrated_schema, _RESPONSES)
        _rebuild_through_the_chain(engine, migrated_schema)
        _reapply_policies(engine, migrated_schema)
        assert _shape(engine, migrated_schema, _RESPONSES) == before
