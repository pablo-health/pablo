# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for the files a form asked for.

The table is registered patient-readable, patient-writable AND
patient-deletable: a patient attaches a photograph of their card, reads
back which sides have arrived, and takes one off again to retake it. The
policies that registration produces are proved here against a schema built
by the real ``create_practice_schema`` (so the policies under test are the
ones that ship) and connected as the ``pablo`` role, which the integration
conftest creates ``NOSUPERUSER NOBYPASSRLS`` exactly as production has it.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion is
preceded by a visibility control on the same connection, so no assertion
can pass because the table was empty, the schema was wrong or the GUC was
never armed. The role's RLS-bypass bits are asserted up front for the same
reason.

Three things beyond isolation are proved here because only a real database
can prove them: the composite foreign key that keeps ``patient_id`` in step
with the assignment's owner, the partial unique index that carries "one
front, one back", and the unique constraint that makes one file answer one
question.

The last section compares the two producers of this table. A freshly
provisioned tenant gets it from ``tenant_template.sql``; an existing one
gets it from the alembic revision. Those are different code paths, and the
classic failure is that one ships and the other does not — which surfaces
far from the cause, as ``relation ... does not exist`` in whatever runs
next.

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

_ARTIFACTS = "patient_intake_artifacts"

# The clinician who created both patients, and so holds a
# ``patient_clinicians`` grant on each.
_TREATING_CLINICIAN = "5b19c704-2e8a-5d31-b4f6-90c27ae1d385"
# A clinician in the same practice with no grant on either patient.
_STRANGER_CLINICIAN = "8d03f261-47b9-5a0c-92e1-3f6b8c05d417"

#: The revision this table's own follows. Rolling a schema back to it and
#: forward again replays the revision under test over a schema without it.
_PARENT_REVISION = "d5e1b7a93c46"


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
    schema = _new_schema(engine, "intake_art")
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
    """A published version with one insurance-card item.

    Written on a clinician connection: a form is the practice's own
    paperwork, carries no ``patient_id``, and is scoped by the tenant schema
    alone. Returns ``(version_id, item_id)``.
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
                "(id, version_id, key, position, item_type, required, label, config, "
                "resign_on_new_version) "
                "VALUES (CAST(:iid AS uuid), CAST(:vid AS uuid), 'card', 0, "
                "'insurance_card', true, 'A photo of your insurance card', "
                'CAST(\'{"sides": "both"}\' AS jsonb), false)'
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
def documents(engine: Engine, tenant_schema: str, two_patients: tuple[str, str]) -> tuple[str, str]:
    """One finished intake upload per patient, written BY that patient.

    Deliberately not seeded clinician-side: the inserts run with only
    ``app.current_patient_id`` armed, so they exercise the write arm
    ``patient_documents`` already has. Returns ``(document_a, document_b)``.
    """
    ids: list[str] = []
    for patient_id in two_patients:
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            ids.append(_upload(conn, patient_id))
            conn.commit()
        finally:
            conn.close()
    return ids[0], ids[1]


@pytest.fixture(scope="module")
def artifacts(  # noqa: PLR0913 — one fixture per table the row points at
    engine: Engine,
    tenant_schema: str,
    two_patients: tuple[str, str],
    published_form: tuple[str, str],
    assignments: tuple[str, str],
    documents: tuple[str, str],
) -> tuple[str, str]:
    """One artifact per patient, written BY that patient.

    If the insert arm the registration grants were missing, FORCE ROW LEVEL
    SECURITY would refuse these writes and this fixture would error rather
    than quietly leaving an empty table for the read tests to pass against.
    """
    _, item_id = published_form
    ids: list[str] = []
    for patient_id, assignment_id, document_id in zip(
        two_patients, assignments, documents, strict=True
    ):
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            ids.append(_attach(conn, patient_id, assignment_id, item_id, document_id))
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


def _assign(conn: Connection, patient_id: str, version_id: str, assignment_id: str) -> str:
    conn.execute(
        text(
            "INSERT INTO patient_intake_assignments "
            "(id, patient_id, version_id, status, assigned_by, assigned_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:vid AS uuid), "
            "'assigned', CAST(:u AS uuid), :now, :now)"
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


def _upload(conn: Connection, patient_id: str, document_id: str | None = None) -> str:
    """A finished ``intake_artifact`` document, as the patient's own path leaves one."""
    document_id = document_id or str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO patient_documents "
            "(id, patient_id, uploaded_by_patient_id, filename, mime_type, gcs_path, "
            "size_bytes, category, created_at, finalized_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:pid AS uuid), "
            "'card.png', 'image/png', :path, 1234, 'intake_artifact', :now, :now)"
        ),
        {
            "id": document_id,
            "pid": patient_id,
            "path": f"tenant/intake_artifact/{document_id}",
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return document_id


def _attach(  # noqa: PLR0913 — one keyword per column the caller varies
    conn: Connection,
    patient_id: str,
    assignment_id: str,
    item_id: str,
    document_id: str,
    *,
    side: str = "front",
    artifact_id: str | None = None,
) -> str:
    artifact_id = artifact_id or str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {_ARTIFACTS} "  # noqa: S608 — module constant, no caller input
            "(id, assignment_id, patient_id, item_id, document_id, side, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:aid AS uuid), CAST(:pid AS uuid), "
            "CAST(:iid AS uuid), CAST(:did AS uuid), :side, :now)"
        ),
        {
            "id": artifact_id,
            "aid": assignment_id,
            "pid": patient_id,
            "iid": item_id,
            "did": document_id,
            "side": side,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return artifact_id


def _visible(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_ARTIFACTS}")).scalars().all()  # noqa: S608
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
                    {"s": tenant_schema, "t": _ARTIFACTS},
                )
                .mappings()
                .first()
            )
        assert row is not None, (
            f"{_ARTIFACTS} missing from a freshly-provisioned tenant — the "
            "migration or the tenant_template.sql regen did not ship"
        )
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["policy_count"] >= 1, "RLS forced with no policy is a silent deny-all"

    def test_it_has_the_delete_arm_and_no_bespoke_policy(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """Four patient arms plus the clinician one.

        The delete arm is what makes retaking a bad photograph possible:
        without a DELETE policy the row is simply invisible to the command,
        so a removal would delete nothing and report success.
        """
        with engine.connect() as conn:
            names = set(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": _ARTIFACTS},
                )
                .scalars()
                .all()
            )
        assert names == {
            "rls_patient_self_read",
            "rls_patient_self_insert",
            "rls_patient_self_write",
            "rls_patient_self_delete",
            "rls_patient_access",
        }, names

    def test_the_blank_form_table_shipped_not_row_scoped(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The practice's stationery, whose boundary is the schema.

        Registered not-row-scoped like ``compliance_items``: RLS is left
        off rather than forced with no policy, which is what a table with
        an ``id`` and no owner would otherwise get — and what would make
        every provisioning test fail.
        """
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT c.relrowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = 'intake_blank_forms'"
                    ),
                    {"s": tenant_schema},
                )
                .mappings()
                .first()
            )
        assert row is not None, "intake_blank_forms missing from a fresh tenant"
        assert row["relrowsecurity"] is False


class TestPatientPrincipalIsolation:
    def test_each_patient_reads_their_own_artifact(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        artifacts: tuple[str, str],
    ) -> None:
        """The control. Without it the refusals below prove nothing.

        The rows were inserted by the fixture under each patient's own
        armed GUC and nothing else — so reaching this assertion already
        means the insert arm admitted a patient attaching their own file.
        """
        for patient_id, artifact_id in zip(two_patients, artifacts, strict=True):
            conn = _as_patient(engine, tenant_schema, patient_id)
            try:
                assert _visible(conn) == {artifact_id}
            finally:
                conn.close()

    def test_a_cannot_see_bs_artifact(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        artifacts: tuple[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        artifact_a, artifact_b = artifacts

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: A sees their own, so the table is not simply empty.
            assert artifact_a in _visible(conn)
            assert artifact_b not in _visible(conn)

            # The IDOR move: armed as A, name B's rows outright.
            named = conn.execute(
                text(
                    f"SELECT id FROM {_ARTIFACTS} "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).scalars()
            assert list(named) == []
        finally:
            conn.close()

    def test_a_cannot_attach_to_bs_form(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """WITH CHECK on the insert arm: A cannot file an artifact as B."""
        patient_a, patient_b = two_patients
        _, assignment_b = assignments
        _, item_id = published_form
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            document_id = _upload(conn, patient_a)
            with pytest.raises(ProgrammingError) as exc:
                _attach(conn, patient_b, assignment_b, item_id, document_id, side="back")
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("artifacts")
    def test_a_cannot_delete_bs_artifact(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The delete arm reaches A's own rows and nothing else.

        The control matters more than usual here: without a DELETE policy
        at all, BOTH statements would report zero rows and the second
        assertion would pass while proving nothing.
        """
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            foreign = conn.execute(
                text(
                    f"DELETE FROM {_ARTIFACTS} "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).rowcount
            assert foreign == 0
            own = conn.execute(
                text(
                    f"DELETE FROM {_ARTIFACTS} "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_a},
            ).rowcount
            assert own >= 1, "the patient delete arm is missing; the refusal above proves nothing"
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("artifacts")
    def test_no_principal_armed_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Fail-closed. The rows exist — the fixtures put them there."""
        conn = _unarmed(engine, tenant_schema)
        try:
            assert _visible(conn) == set()
        finally:
            conn.close()


class TestClinicianAccess:
    @pytest.mark.usefixtures("artifacts")
    def test_treating_clinician_sees_both_patients(
        self, engine: Engine, tenant_schema: str, artifacts: tuple[str, str]
    ) -> None:
        """``has_patient_access`` reaches the rows a grant covers."""
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert set(artifacts) <= _visible(conn)
        finally:
            conn.close()

    @pytest.mark.usefixtures("artifacts")
    def test_stranger_clinician_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Same practice, no grant on either patient. The control is above."""
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            assert _visible(conn) == set()
        finally:
            conn.close()


class TestIntegrityConstraints:
    """What the database refuses regardless of who is asking."""

    def test_an_artifact_cannot_claim_a_patient_its_assignment_does_not_have(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """The composite FK: the denormalized column cannot drift.

        Run as the treating clinician, who has a grant on BOTH patients —
        so row security admits the write and the only thing left to refuse
        it is the foreign key.
        """
        patient_a, patient_b = two_patients
        assignment_a, _ = assignments
        _, item_id = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # Control: the same insert with the matching patient succeeds.
            # Undone through a savepoint rather than a rollback, because a
            # rollback would also undo this connection's ``search_path``
            # and the refusal below would be a missing table instead.
            savepoint = conn.begin_nested()
            document_id = _upload(conn, patient_a)
            _attach(conn, patient_a, assignment_a, item_id, document_id, side="back")
            savepoint.rollback()

            other = _upload(conn, patient_b)
            with pytest.raises(IntegrityError):
                _attach(conn, patient_b, assignment_a, item_id, other, side="back")
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("artifacts")
    def test_one_photo_per_side_per_question(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """The partial unique index: a card has one front and one back."""
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        _, item_id = published_form
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            document_id = _upload(conn, patient_a)
            with pytest.raises(IntegrityError) as exc:
                _attach(conn, patient_a, assignment_a, item_id, document_id, side="front")
            assert "uq_patient_intake_artifacts_side" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("artifacts")
    def test_the_other_side_is_not_a_clash(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        """The index is per side, so the back is not refused by the front."""
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        _, item_id = published_form
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            document_id = _upload(conn, patient_a)
            _attach(conn, patient_a, assignment_a, item_id, document_id, side="back")
            conn.rollback()
        finally:
            conn.close()

    def test_one_file_answers_one_question(  # noqa: PLR0913 — one fixture per table
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
        documents: tuple[str, str],
    ) -> None:
        """The unique ``document_id``: the same photo cannot be both sides."""
        patient_a, _ = two_patients
        assignment_a, _ = assignments
        document_a, _ = documents
        _, item_id = published_form
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(IntegrityError):
                _attach(conn, patient_a, assignment_a, item_id, document_a, side="back")
            conn.rollback()
        finally:
            conn.close()

    def test_an_unknown_side_is_refused(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
        assignments: tuple[str, str],
    ) -> None:
        _, patient_b = two_patients
        _, assignment_b = assignments
        _, item_id = published_form
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            document_id = _upload(conn, patient_b)
            with pytest.raises(IntegrityError) as exc:
                _attach(conn, patient_b, assignment_b, item_id, document_id, side="middle")
            assert "ck_patient_intake_artifacts_side" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("artifacts")
    def test_deleting_an_assignment_takes_its_artifacts(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        published_form: tuple[str, str],
    ) -> None:
        """ON DELETE CASCADE, so a removed request leaves no orphan rows."""
        patient_a, _ = two_patients
        version_id, item_id = published_form
        assignment_id = str(uuid.uuid4())
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            other_version = _second_version(conn, version_id)
            _move_item(conn, item_id, other_version)
            _assign(conn, patient_a, other_version, assignment_id)
            document_id = _upload(conn, patient_a)
            artifact_id = _attach(conn, patient_a, assignment_id, item_id, document_id)
            assert artifact_id in _visible(conn)
            conn.execute(
                text("DELETE FROM patient_intake_assignments WHERE id = CAST(:id AS uuid)"),
                {"id": assignment_id},
            )
            assert artifact_id not in _visible(conn)
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("artifacts")
    def test_deleting_the_document_takes_its_artifact(
        self,
        engine: Engine,
        tenant_schema: str,
        documents: tuple[str, str],
        artifacts: tuple[str, str],
    ) -> None:
        """ON DELETE CASCADE the other way, for the hard-delete sweep.

        Today a removed artifact tombstones its document rather than
        deleting the row, so this path is not what the route uses. It is
        here because the eventual storage-cleanup job WILL hard-delete, and
        an orphan artifact pointing at nothing would then be a row the form
        reads as an answer.
        """
        document_a, _ = documents
        artifact_a, _ = artifacts
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert artifact_a in _visible(conn)
            conn.execute(
                text("DELETE FROM patient_documents WHERE id = CAST(:id AS uuid)"),
                {"id": document_a},
            )
            assert artifact_a not in _visible(conn)
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


def _move_item(conn: Connection, item_id: str, version_id: str) -> None:
    """Point the item at another version, so the cascade test can reuse it.

    The artifact's foreign key names the ITEM, and an item belongs to one
    version — so a second assignment of a second version needs the item to
    be reachable from it. Rolled back with everything else.
    """
    conn.execute(
        text(
            "UPDATE intake_item_definitions SET version_id = CAST(:vid AS uuid) "
            "WHERE id = CAST(:iid AS uuid)"
        ),
        {"vid": version_id, "iid": item_id},
    )


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
    """Drop the tables, roll the schema back one revision, migrate forward.

    This is how an existing tenant gets them: not from the template but
    from the revision, replayed here over a schema that does not have them.
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_ARTIFACTS} CASCADE"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.intake_blank_forms CASCADE"))
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
    schema = _new_schema(engine, "intake_art_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant that got the tables from the revision instead."""
    schema = _new_schema(engine, "intake_art_migrated")
    _rebuild_through_the_chain(engine, schema)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers of these tables must land the same shape.

    If they drift, the symptom appears wherever a fresh tenant is next
    provisioned, not here — so they are compared directly.
    """

    @pytest.mark.parametrize("table", [_ARTIFACTS, "intake_blank_forms"])
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
        before = _shape(engine, migrated_schema, _ARTIFACTS)
        _rebuild_through_the_chain(engine, migrated_schema)
        _reapply_policies(engine, migrated_schema)
        assert _shape(engine, migrated_schema, _ARTIFACTS) == before
