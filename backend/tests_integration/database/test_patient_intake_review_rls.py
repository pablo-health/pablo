# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for the intake review log.

``patient_intake_review_events`` is registered patient-READABLE and
patient-writable, and it is the one table in this feature where the two are
not the same set of rows. A patient reads the note asking them to redo a
question — a row the practice wrote — and may write exactly one kind of row
themselves, the one that says they did it. Everything else on the table is
something the practice did, and a policy that let a patient write any row
they can read would let them mint their own acceptance.

That asymmetry is the thing only a real database can prove, so it is proved
here, against a schema built by the real ``create_practice_schema`` and
connected as the ``pablo`` role, which the integration conftest creates
``NOSUPERUSER NOBYPASSRLS`` exactly as production has it.

**Non-vacuity is enforced, not hoped for.** Every invisibility and refusal
assertion is preceded by a visibility or success control on the same
connection, so no assertion can pass because the table was empty, the schema
was wrong or the GUC was never armed. The role's RLS-bypass bits are
asserted up front for the same reason.

Two things beyond isolation are proved because only a real database can:
the composite foreign key that keeps ``patient_id`` in step with the
assignment's owner, and the successor arrangement on
``patient_intake_responses`` — a replaced answer keeps its value and gains a
pointer, and the partial unique index admits both rows.

The last section compares the two producers of the table. A freshly
provisioned tenant gets it from ``tenant_template.sql``; an existing one
gets it from the alembic revision. Those are different code paths, and the
classic failure is that one ships and the other does not — which surfaces
far from the cause.

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
from sqlalchemy.exc import IntegrityError

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

_EVENTS = "patient_intake_review_events"
_RESPONSES = "patient_intake_responses"

_TREATING_CLINICIAN = "5b19c704-2e8a-5d31-b4f6-90c27ae1d385"
_STRANGER_CLINICIAN = "8d03f261-47b9-5a0c-92e1-3f6b8c05d417"

#: The revision this table's own follows. Rolling a schema back to it and
#: forward again replays the revision under test over a schema without it.
_PARENT_REVISION = "f3c81a4e72d9"


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
    schema = _new_schema(engine, "intake_review")
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
    return patient_a, patient_b


@pytest.fixture(scope="module")
def published_form(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    """A published version with one written question. ``(version_id, item_id)``."""
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
                "(id, version_id, key, position, item_type, required, label, config) "
                "VALUES (CAST(:iid AS uuid), CAST(:vid AS uuid), 'reason', 1, 'reason', "
                "true, NULL, '{}'::jsonb)"
            ),
            {"iid": item_id, "vid": version_id},
        )
    return version_id, item_id


@pytest.fixture(scope="module")
def assignments(
    engine: Engine,
    tenant_schema: str,
    two_patients: tuple[str, str],
    published_form: tuple[str, str],
) -> tuple[str, str]:
    """One submitted assignment per patient, sent by the treating clinician."""
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
def correction_requests(
    engine: Engine,
    tenant_schema: str,
    two_patients: tuple[str, str],
    published_form: tuple[str, str],
    assignments: tuple[str, str],
) -> tuple[str, str]:
    """One correction request per patient, written by the treating clinician.

    Deliberately written clinician-side: this is the kind a patient may read
    and may not write, so seeding it through the patient arm would prove the
    wrong thing — and would fail, which is what the refusal tests below say.
    """
    _, item_id = published_form
    ids: list[str] = []
    conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
    try:
        for patient_id, assignment_id in zip(two_patients, assignments, strict=True):
            ids.append(
                _event(
                    conn,
                    patient_id,
                    assignment_id,
                    kind="correction_requested",
                    item_ids=item_id,
                    note="Have another look at this one?",
                    created_by=_TREATING_CLINICIAN,
                )
            )
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
    # Committed so the schema and the GUC survive a later rollback: a test
    # that rolls a refused write back must not lose the search_path with it.
    conn.commit()
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
    conn.commit()
    return conn


def _unarmed(engine: Engine, schema: str) -> Connection:
    """A connection with no principal armed at all."""
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("RESET app.current_patient_id"))
    conn.commit()
    return conn


#: Version numbers for the throwaway versions below, so each starts above
#: anything the fixtures published.
_next_version = 100


def _another_published_version(conn: Connection, version_id: str) -> str:
    """Another published version of the same form.

    The partial unique index allows one live assignment per patient per
    VERSION, so a test that needs a second assignment for the same patient
    needs a second version to hang it on. Publishing one is the honest way
    to get there: the alternative is parking the extra assignment in a
    terminal status, which would quietly change what the test is about.
    """
    global _next_version  # noqa: PLW0603 — module-local counter for one helper
    _next_version += 1
    new_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO intake_packet_versions "
            "(id, template_id, version, published_at, published_by, created_at) "
            "SELECT CAST(:new AS uuid), template_id, :n, now(), published_by, now() "
            "FROM intake_packet_versions WHERE id = CAST(:old AS uuid)"
        ),
        {"new": new_id, "old": version_id, "n": _next_version},
    )
    return new_id


def _assign(conn: Connection, patient_id: str, version_id: str, assignment_id: str) -> str:
    conn.execute(
        text(
            "INSERT INTO patient_intake_assignments "
            "(id, patient_id, version_id, status, assigned_by, assigned_at, "
            "submitted_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:vid AS uuid), "
            "'submitted', CAST(:u AS uuid), :now, :now, :now)"
        ),
        {
            "id": assignment_id,
            "pid": patient_id,
            "vid": version_id,
            "u": _TREATING_CLINICIAN,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return assignment_id


def _event(  # noqa: PLR0913 — one keyword per column the caller varies
    conn: Connection,
    patient_id: str,
    assignment_id: str,
    *,
    kind: str,
    item_ids: str | None = None,
    note: str | None = None,
    created_by: str | None = None,
    event_id: str | None = None,
) -> str:
    event_id = event_id or str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {_EVENTS} "  # noqa: S608 — module constant, no caller input
            "(id, assignment_id, patient_id, kind, item_ids, note_to_patient, "
            "created_by, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:aid AS uuid), CAST(:pid AS uuid), "
            ":kind, CAST(:items AS jsonb), :note, CAST(:by AS uuid), :now)"
        ),
        {
            "id": event_id,
            "aid": assignment_id,
            "pid": patient_id,
            "kind": kind,
            "items": "[]" if item_ids is None else f'["{item_ids}"]',
            "note": note,
            "by": created_by,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return event_id


def _answer(  # noqa: PLR0913 — one keyword per column the caller varies
    conn: Connection,
    patient_id: str,
    assignment_id: str,
    item_id: str,
    *,
    text_value: str,
    draft: bool = False,
    provenance: str = "patient",
    response_id: str | None = None,
) -> str:
    response_id = response_id or str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {_RESPONSES} "  # noqa: S608 — module constant, no caller input
            "(id, assignment_id, patient_id, item_id, value, draft, provenance, "
            "superseded_by, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:aid AS uuid), CAST(:pid AS uuid), "
            "CAST(:iid AS uuid), CAST(:val AS jsonb), :draft, :prov, NULL, :now, :now)"
        ),
        {
            "id": response_id,
            "aid": assignment_id,
            "pid": patient_id,
            "iid": item_id,
            "val": f'{{"text": "{text_value}"}}',
            "draft": draft,
            "prov": provenance,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return response_id


def _visible(conn: Connection) -> set[str]:
    return set(conn.execute(text(f"SELECT id::text FROM {_EVENTS}")).scalars().all())  # noqa: S608


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


class TestTableShipped:
    """The migration and the template regen both landed."""

    def test_table_present_with_rls_forced_and_a_policy(
        self, engine: Engine, tenant_schema: str
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
                    {"s": tenant_schema, "t": _EVENTS},
                )
                .mappings()
                .first()
            )
        assert row is not None, (
            f"{_EVENTS} missing from a freshly-provisioned tenant — the "
            "migration or the tenant_template.sql regen did not ship"
        )
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["policy_count"] >= 1, "RLS forced with no policy is a silent deny-all"

    def test_it_needed_no_bespoke_policy(self, engine: Engine, tenant_schema: str) -> None:
        """The denormalized ``patient_id`` is what avoids one."""
        with engine.connect() as conn:
            names = set(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": _EVENTS},
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

    def test_the_responses_table_records_where_an_answer_came_from(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """``provenance`` reached a freshly-provisioned tenant too."""
        with engine.connect() as conn:
            column = (
                conn.execute(
                    text(
                        "SELECT column_default FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t "
                        "AND column_name = 'provenance'"
                    ),
                    {"s": tenant_schema, "t": _RESPONSES},
                )
                .scalars()
                .first()
            )
        assert column is not None, "provenance missing from a fresh tenant"
        assert "patient" in column


class TestPatientPrincipalIsolation:
    def test_each_patient_reads_the_correction_asked_of_them(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        correction_requests: tuple[str, str],
    ) -> None:
        """Visibility control. Without this the invisibility tests prove nothing."""
        for patient_id, event_id in zip(two_patients, correction_requests, strict=True):
            conn = _as_patient(engine, tenant_schema, patient_id)
            try:
                assert _visible(conn) == {event_id}
                note = conn.execute(
                    text(
                        f"SELECT note_to_patient FROM {_EVENTS} "  # noqa: S608
                        "WHERE id = CAST(:e AS uuid)"
                    ),
                    {"e": event_id},
                ).scalar()
                assert note == "Have another look at this one?"
            finally:
                conn.close()

    def test_a_cannot_see_the_correction_asked_of_b(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        correction_requests: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        _, event_b = correction_requests
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert event_b not in _visible(conn)
            # The IDOR move: armed as A, name B's row by primary key.
            named = (
                conn.execute(
                    text(f"SELECT id FROM {_EVENTS} WHERE id = CAST(:e AS uuid)"),  # noqa: S608
                    {"e": event_b},
                )
                .scalars()
                .all()
            )
            assert named == []
        finally:
            conn.close()

    def test_no_principal_armed_sees_nothing(
        self, engine: Engine, tenant_schema: str, correction_requests: tuple[str, str]
    ) -> None:
        conn = _unarmed(engine, tenant_schema)
        try:
            visible = _visible(conn)
            assert visible == set()
            assert not set(correction_requests) & visible
        finally:
            conn.close()


class TestThePatientsOneKind:
    """Read and write are not the same set of rows, and the policy says so."""

    def test_a_patient_may_record_that_they_made_the_corrections(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """Success control. Without it, every refusal below could be a dead arm."""
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            event_id = _event(conn, patient_a, assignment_a, kind="corrected")
            conn.commit()
            assert event_id in _visible(conn)
        finally:
            conn.close()

    def test_a_patient_cannot_accept_their_own_form(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """The whole reason the write arm is narrower than the read arm."""
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(Exception, match="row-level security"):
                _event(conn, patient_a, assignment_a, kind="accepted")
            conn.rollback()
        finally:
            conn.close()

    def test_a_patient_cannot_ask_themselves_for_a_correction(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(Exception, match="row-level security"):
                _event(
                    conn,
                    patient_a,
                    assignment_a,
                    kind="correction_requested",
                    note="Asking myself.",
                )
            conn.rollback()
        finally:
            conn.close()

    def test_a_patient_cannot_write_an_event_onto_bs_form(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        _, assignment_b = assignments
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(Exception, match="row-level security"):
                _event(conn, patient_b, assignment_b, kind="corrected")
            conn.rollback()
        finally:
            conn.close()


class TestClinicianAccess:
    def test_the_treating_clinician_sees_both_patients(
        self, engine: Engine, tenant_schema: str, correction_requests: tuple[str, str]
    ) -> None:
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert set(correction_requests) <= _visible(conn)
        finally:
            conn.close()

    def test_a_clinician_with_no_grant_sees_nothing(
        self, engine: Engine, tenant_schema: str, correction_requests: tuple[str, str]
    ) -> None:
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            visible = _visible(conn)
            assert visible == set()
            assert not set(correction_requests) & visible
        finally:
            conn.close()


class TestIntegrityConstraints:
    def test_an_event_cannot_claim_a_patient_its_assignment_does_not_have(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """The composite foreign key, which is what keeps the copy honest."""
        patient_a, patient_b = two_patients
        assignment_a, _ = assignments
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # Control: the same insert with the right pair is accepted.
            _event(conn, patient_a, assignment_a, kind="accepted")
            conn.rollback()

            with pytest.raises(IntegrityError):
                _event(conn, patient_b, assignment_a, kind="accepted")
            conn.rollback()
        finally:
            conn.close()

    def test_an_unknown_kind_is_refused(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            with pytest.raises(IntegrityError):
                _event(conn, patient_a, assignment_a, kind="shredded")
            conn.rollback()
        finally:
            conn.close()

    def test_deleting_an_assignment_takes_its_events(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        version_id, item_id = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assignment_id = str(uuid.uuid4())
            _assign(conn, patient_a, _another_published_version(conn, version_id), assignment_id)
            event_id = _event(conn, patient_a, assignment_id, kind="accepted", item_ids=item_id)
            conn.commit()
            assert event_id in _visible(conn)

            conn.execute(
                text("DELETE FROM patient_intake_assignments WHERE id = CAST(:a AS uuid)"),
                {"a": assignment_id},
            )
            conn.commit()
            assert event_id not in _visible(conn)
        finally:
            conn.close()


class TestSuccessorAnswers:
    """A replaced answer keeps its value. Only a pointer changes."""

    def test_a_successor_leaves_the_frozen_row_alone(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        version_id, item_id = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assignment_id = str(uuid.uuid4())
            _assign(conn, patient_a, _another_published_version(conn, version_id), assignment_id)
            first = _answer(
                conn, patient_a, assignment_id, item_id, text_value="What the patient wrote"
            )
            second = _answer(
                conn,
                patient_a,
                assignment_id,
                item_id,
                text_value="What the practice wrote down",
                provenance="clinician",
            )
            conn.execute(
                text(
                    f"UPDATE {_RESPONSES} SET superseded_by = CAST(:new AS uuid) "  # noqa: S608
                    "WHERE id = CAST(:old AS uuid)"
                ),
                {"new": second, "old": first},
            )
            conn.commit()

            rows = (
                conn.execute(
                    text(
                        "SELECT id::text, value->>'text' AS body, provenance, "  # noqa: S608
                        f"superseded_by::text FROM {_RESPONSES} "
                        "WHERE assignment_id = CAST(:a AS uuid) ORDER BY created_at, id"
                    ),
                    {"a": assignment_id},
                )
                .mappings()
                .all()
            )
            by_id = {row["id"]: row for row in rows}
            assert by_id[first]["body"] == "What the patient wrote"
            assert by_id[first]["provenance"] == "patient"
            assert by_id[first]["superseded_by"] == second
            assert by_id[second]["provenance"] == "clinician"
            assert by_id[second]["superseded_by"] is None
        finally:
            conn.close()

    def test_an_unknown_provenance_is_refused(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        version_id, item_id = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assignment_id = str(uuid.uuid4())
            _assign(conn, patient_a, _another_published_version(conn, version_id), assignment_id)
            with pytest.raises(IntegrityError):
                _answer(
                    conn,
                    patient_a,
                    assignment_id,
                    item_id,
                    text_value="Who wrote this?",
                    provenance="somebody",
                )
            conn.rollback()
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# The two producers of the table
# ---------------------------------------------------------------------------


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
    """Drop the table and the column, roll back one revision, migrate forward.

    This is how an existing tenant gets both: not from the template but from
    the revision, replayed here over a schema that does not have them.
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_EVENTS} CASCADE"))
        conn.execute(
            text(
                f"ALTER TABLE {schema}.{_RESPONSES} "
                "DROP CONSTRAINT IF EXISTS ck_patient_intake_responses_provenance"
            )
        )
        conn.execute(text(f"ALTER TABLE {schema}.{_RESPONSES} DROP COLUMN IF EXISTS provenance"))
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
    schema = _new_schema(engine, "review_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant that got the table from the revision instead."""
    schema = _new_schema(engine, "review_migrated")
    _rebuild_through_the_chain(engine, schema)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers must land the same shape.

    If they drift, the symptom appears wherever a fresh tenant is next
    provisioned, not here — so they are compared directly.
    """

    def test_template_and_chain_produce_the_same_table(
        self, engine: Engine, fresh_schema: str, migrated_schema: str
    ) -> None:
        fresh = _shape(engine, fresh_schema, _EVENTS)
        migrated = _shape(engine, migrated_schema, _EVENTS)
        assert fresh["columns"], f"{_EVENTS} absent from the template-built schema"
        assert migrated["columns"], f"{_EVENTS} absent from the chain-built schema"
        assert fresh == migrated

    def test_template_and_chain_agree_about_provenance(
        self, engine: Engine, fresh_schema: str, migrated_schema: str
    ) -> None:
        fresh = _shape(engine, fresh_schema, _RESPONSES)
        migrated = _shape(engine, migrated_schema, _RESPONSES)
        assert any("provenance" in column for column in fresh["columns"])
        assert fresh == migrated

    def test_the_revision_is_idempotent(self, engine: Engine, migrated_schema: str) -> None:
        """Fanned out once per practice schema, so replaying it must be a no-op."""
        before = (
            _shape(engine, migrated_schema, _EVENTS),
            _shape(engine, migrated_schema, _RESPONSES),
        )
        _rebuild_through_the_chain(engine, migrated_schema)
        _reapply_policies(engine, migrated_schema)
        after = (
            _shape(engine, migrated_schema, _EVENTS),
            _shape(engine, migrated_schema, _RESPONSES),
        )
        assert after == before
