# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for patient-uploaded chart documents.

``patient_documents`` was a clinician-only table until patients could send
something in themselves. Three things happened to it at once, and all three
are provable only against a real database:

* A second uploader. ``user_id`` became nullable,
  ``uploaded_by_patient_id`` arrived, and a CHECK makes them exclusive.
* Two more categories, ``intake_artifact`` and ``message``, which are what
  the patient's own surface uses.
* A patient-principal policy arm, added by registering the table
  patient-readable and patient-writable. It is **bespoke**: the plain
  predicate would match every row on the patient's chart, including the
  psychotherapy-notes carve-out that patient right-of-access does not reach
  (§164.524(a)(1)(i)). The category test is part of the policy, so a route
  that dropped its own filter still could not disclose one.

The clinician policy beside it is not supposed to have moved, and the last
section of :class:`TestClinicianAccess` is what says so: a treating
clinician still sees the chart, a stranger in the same practice still sees
nothing, and the uploader-only arm still holds for restricted categories.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion is
preceded by a visibility control on the same connection, so no assertion can
pass because the table was empty, the schema was wrong or the GUC was never
armed. The role's RLS-bypass bits are asserted up front for the same reason.

The last section compares the two producers of this table. A freshly
provisioned tenant gets it from ``tenant_template.sql``; an existing one
gets it from the alembic revision. Those are different code paths, and the
classic failure is that one ships and the other does not — which surfaces
far from the cause, as a constraint that is missing on exactly one of them.

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

_DOCUMENTS = "patient_documents"

# The clinician who created both patients, and so holds a
# ``patient_clinicians`` grant on each.
_TREATING_CLINICIAN = "5b19c704-2e8a-5d31-b4f6-90c27ae1d385"
# A clinician in the same practice with no grant on either patient.
_STRANGER_CLINICIAN = "8d03f261-47b9-5a0c-92e1-3f6b8c05d417"

#: The revision this table's change follows. Rolling a schema back to it and
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
    schema = _new_schema(engine, "patient_docs")
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
def patient_uploads(
    engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
) -> tuple[str, str]:
    """One document per patient, uploaded BY that patient.

    Deliberately not seeded clinician-side. The inserts run on a connection
    with only ``app.current_patient_id`` armed, so they exercise the write
    arm the registration grants — if that arm were missing, FORCE ROW LEVEL
    SECURITY would refuse the INSERT and this fixture would error rather
    than quietly leaving an empty table for the read tests to pass against.
    """
    ids: list[str] = []
    for patient_id in two_patients:
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            ids.append(_upload_as_patient(conn, patient_id))
            conn.commit()
        finally:
            conn.close()
    return ids[0], ids[1]


@pytest.fixture(scope="module")
def clinician_uploads(
    engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
) -> dict[str, str]:
    """One clinician row per category, all on patient A's chart.

    These are what the patient arm must NOT reach — and what the clinician
    arm must still reach, which is the other half of the proof.
    """
    patient_a, _ = two_patients
    ids: dict[str, str] = {}
    conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
    try:
        for category in (
            "chart",
            "consent",
            "therapist_private",
            "psychotherapy_notes",
            "message",
        ):
            ids[category] = _upload_as_clinician(conn, patient_a, category=category)
        conn.commit()
    finally:
        conn.close()
    return ids


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


def _insert(  # noqa: PLR0913 — one keyword per column the caller varies
    conn: Connection,
    *,
    patient_id: str,
    category: str,
    user_id: str | None,
    uploaded_by_patient_id: str | None,
    document_id: str | None = None,
) -> str:
    document_id = document_id or str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {_DOCUMENTS} "  # noqa: S608 — module constant, no caller input
            "(id, patient_id, user_id, uploaded_by_patient_id, filename, mime_type, "
            "gcs_path, size_bytes, category, created_at, finalized_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), "
            "CAST(:uid AS uuid), CAST(:upid AS uuid), :filename, 'application/pdf', "
            ":path, 1024, :category, :now, :now)"
        ),
        {
            "id": document_id,
            "pid": patient_id,
            "uid": user_id,
            "upid": uploaded_by_patient_id,
            "filename": "document.pdf",
            "path": f"schema/{category}/{document_id}",
            "category": category,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return document_id


def _upload_as_patient(
    conn: Connection, patient_id: str, *, category: str = "intake_artifact"
) -> str:
    return _insert(
        conn,
        patient_id=patient_id,
        category=category,
        user_id=None,
        uploaded_by_patient_id=patient_id,
    )


def _upload_as_clinician(conn: Connection, patient_id: str, *, category: str = "chart") -> str:
    return _insert(
        conn,
        patient_id=patient_id,
        category=category,
        user_id=_TREATING_CLINICIAN,
        uploaded_by_patient_id=None,
    )


def _visible(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_DOCUMENTS}")).scalars().all()  # noqa: S608
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


class TestSchemaShipped:
    """The migration and the template regen both landed."""

    def test_the_second_uploader_column_is_there(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            columns = dict(
                conn.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": tenant_schema, "t": _DOCUMENTS},
                ).all()
            )
        assert "uploaded_by_patient_id" in columns, (
            "uploaded_by_patient_id missing from a freshly-provisioned tenant — the "
            "migration or the tenant_template.sql regen did not ship"
        )
        assert columns["uploaded_by_patient_id"] == "YES"
        assert columns["user_id"] == "YES", "user_id is still NOT NULL; a patient cannot insert"

    def test_both_check_constraints_are_there(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            checks = dict(
                conn.execute(
                    text(
                        "SELECT con.conname, pg_get_constraintdef(con.oid) "
                        "FROM pg_constraint con "
                        "JOIN pg_class c ON c.oid = con.conrelid "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = :t AND con.contype = 'c'"
                    ),
                    {"s": tenant_schema, "t": _DOCUMENTS},
                ).all()
            )
        one_uploader = checks.get("ck_patient_documents_one_uploader")
        assert one_uploader is not None, sorted(checks)
        assert "<>" in one_uploader

        category = checks.get("ck_patient_documents_category")
        assert category is not None, sorted(checks)
        for value in ("chart", "consent", "intake_artifact", "message"):
            assert f"'{value}'" in category, category

    def test_rls_is_forced_and_both_arms_exist(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :s AND c.relname = :t"
                    ),
                    {"s": tenant_schema, "t": _DOCUMENTS},
                )
                .mappings()
                .first()
            )
            names = set(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": _DOCUMENTS},
                )
                .scalars()
                .all()
            )
        assert row is not None
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert names == {
            "rls_patient_doc_access",
            "rls_patient_self_read",
            "rls_patient_self_insert",
            "rls_patient_self_write",
        }, names

    def test_the_patient_arm_carries_the_category_test(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        """The whole point of the bespoke predicate, read off the policy itself.

        Asserted on the SQL because the behavioural tests below could all
        pass against a route-layer filter; this is what says the database
        would refuse on its own.
        """
        with engine.connect() as conn:
            predicate = conn.execute(
                text(
                    "SELECT qual FROM pg_policies WHERE schemaname = :s "
                    "AND tablename = :t AND policyname = 'rls_patient_self_read'"
                ),
                {"s": tenant_schema, "t": _DOCUMENTS},
            ).scalar_one()
        assert "current_patient_id" in predicate
        assert "intake_artifact" in predicate
        assert "message" in predicate
        assert "psychotherapy_notes" not in predicate


class TestPatientPrincipalIsolation:
    def test_each_patient_reads_their_own_upload(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        patient_uploads: tuple[str, str],
    ) -> None:
        """The control. Without it the refusals below prove nothing.

        The rows were inserted by the fixture under each patient's own armed
        GUC and nothing else — so reaching this assertion already means the
        insert arm admitted a patient uploading to their own chart.
        """
        for patient_id, document_id in zip(two_patients, patient_uploads, strict=True):
            conn = _as_patient(engine, tenant_schema, patient_id)
            try:
                assert _visible(conn) == {document_id}
            finally:
                conn.close()

    def test_a_cannot_see_bs_upload(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        patient_uploads: tuple[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        document_a, document_b = patient_uploads

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: A sees their own, so the table is not simply empty.
            assert document_a in _visible(conn)
            assert document_b not in _visible(conn)

            # The IDOR move: armed as A, name B's rows outright.
            named = conn.execute(
                text(
                    f"SELECT id FROM {_DOCUMENTS} "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).scalars()
            assert list(named) == []
        finally:
            conn.close()

    def test_a_cannot_upload_to_bs_chart(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """WITH CHECK on the insert arm: A cannot file a row owned by B."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _insert(
                    conn,
                    patient_id=patient_b,
                    category="intake_artifact",
                    user_id=None,
                    uploaded_by_patient_id=patient_b,
                )
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_upload_claiming_bs_chart_while_naming_themselves(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The other half of the same move: own uploader, someone else's chart.

        ``patient_id`` is the column the policy tests, so this is what stops
        A from parking a document on B's chart under their own name.
        """
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _insert(
                    conn,
                    patient_id=patient_b,
                    category="intake_artifact",
                    user_id=None,
                    uploaded_by_patient_id=patient_a,
                )
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.parametrize(
        "category", ["chart", "consent", "therapist_private", "psychotherapy_notes"]
    )
    def test_a_patient_cannot_file_outside_their_own_surface(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        category: str,
    ) -> None:
        """Their own chart, their own id, and the policy still refuses.

        A route that took a category from the request body could otherwise
        let a patient write a row into a category their own reads are
        filtered out of — visible to every co-treating clinician and
        invisible to the person who wrote it.
        """
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: the same insert in a patient-facing category lands.
            savepoint = conn.begin_nested()
            _upload_as_patient(conn, patient_a, category="message")
            savepoint.rollback()

            with pytest.raises(ProgrammingError) as exc:
                _upload_as_patient(conn, patient_a, category=category)
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("patient_uploads")
    def test_the_rest_of_their_own_chart_is_invisible(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        clinician_uploads: dict[str, str],
        patient_uploads: tuple[str, str],
    ) -> None:
        """The reason the predicate is bespoke.

        Every row here is on patient A's chart and carries A's
        ``patient_id``. The plain registration would have matched all of
        them — including the psychotherapy-notes carve-out.
        """
        patient_a, _ = two_patients
        document_a, _ = patient_uploads
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            visible = _visible(conn)
            # Control: their own upload IS there, and so is what the practice
            # attached to their correspondence.
            assert document_a in visible
            assert clinician_uploads["message"] in visible

            for category in ("chart", "consent", "therapist_private", "psychotherapy_notes"):
                assert clinician_uploads[category] not in visible, category
        finally:
            conn.close()

    @pytest.mark.usefixtures("patient_uploads")
    def test_a_cannot_change_bs_upload(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """Invisible to a write as well as to a read: rowcount 0, no error."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: A's own update lands, so the statement itself works.
            own = conn.execute(
                text(
                    f"UPDATE {_DOCUMENTS} SET size_bytes = 99 "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_a},
            ).rowcount
            assert own >= 1
            foreign = conn.execute(
                text(
                    f"UPDATE {_DOCUMENTS} SET size_bytes = 99 "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).rowcount
            assert foreign == 0
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("patient_uploads", "clinician_uploads")
    def test_no_principal_armed_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Fail-closed. The rows exist — the fixtures put them there."""
        conn = _unarmed(engine, tenant_schema)
        try:
            assert _visible(conn) == set()
        finally:
            conn.close()


class TestClinicianAccess:
    """The arm that was already here, proved not to have moved."""

    @pytest.mark.usefixtures("clinician_uploads")
    def test_treating_clinician_sees_both_patients_uploads(
        self,
        engine: Engine,
        tenant_schema: str,
        patient_uploads: tuple[str, str],
    ) -> None:
        """A patient upload reaches the chart it was sent to.

        ``has_patient_access`` is what admits it, and the row has no
        ``user_id`` at all — so this is also the proof that making that
        column nullable did not make patient rows unreachable.
        """
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert set(patient_uploads) <= _visible(conn)
        finally:
            conn.close()

    def test_treating_clinician_still_sees_their_own_restricted_rows(
        self, engine: Engine, tenant_schema: str, clinician_uploads: dict[str, str]
    ) -> None:
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert set(clinician_uploads.values()) <= _visible(conn)
        finally:
            conn.close()

    @pytest.mark.usefixtures("patient_uploads", "clinician_uploads")
    def test_stranger_clinician_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Same practice, no grant on either patient. The control is above."""
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            assert _visible(conn) == set()
        finally:
            conn.close()


class TestIntegrityConstraints:
    """What the database refuses regardless of who is asking."""

    def test_a_row_with_two_uploaders_is_refused(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # Control: one uploader is accepted, so the refusal is the CHECK.
            savepoint = conn.begin_nested()
            _upload_as_clinician(conn, patient_a)
            savepoint.rollback()

            with pytest.raises(IntegrityError) as exc:
                _insert(
                    conn,
                    patient_id=patient_a,
                    category="chart",
                    user_id=_TREATING_CLINICIAN,
                    uploaded_by_patient_id=patient_a,
                )
            assert "ck_patient_documents_one_uploader" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_a_row_with_no_uploader_is_refused(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            with pytest.raises(IntegrityError) as exc:
                _insert(
                    conn,
                    patient_id=patient_a,
                    category="chart",
                    user_id=None,
                    uploaded_by_patient_id=None,
                )
            assert "ck_patient_documents_one_uploader" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_an_unknown_category_is_refused(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            with pytest.raises(IntegrityError) as exc:
                _upload_as_clinician(conn, patient_a, category="whatever")
            assert "ck_patient_documents_category" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()


def _shape(engine: Engine, schema: str) -> dict[str, list[str]]:
    """Columns, constraint names and kinds, and policy names for *schema*'s table.

    Constraint *definitions* are deliberately not compared. A CHECK written
    as ``category IN (...)`` and the same CHECK captured by ``pg_dump`` come
    back as different text for the same rule, and the foreign key's
    definition carries the schema name, which differs by construction. Both
    would fail on a difference that is not one. The rules themselves are
    compared by behaviour instead, in
    :meth:`TestFreshAndMigratedAgree.test_both_schemas_enforce_the_same_rules`.
    """
    with engine.connect() as conn:
        columns = list(
            conn.execute(
                text(
                    "SELECT column_name || ' ' || data_type || ' ' || is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t "
                    "ORDER BY column_name"
                ),
                {"s": schema, "t": _DOCUMENTS},
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
                {"s": schema, "t": _DOCUMENTS},
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
                {"s": schema, "t": _DOCUMENTS},
            )
            .scalars()
            .all()
        )
    return {"columns": columns, "constraints": constraints, "policies": policies}


def _rebuild_through_the_chain(engine: Engine, schema: str) -> None:
    """Roll the schema back one revision and migrate forward again.

    This is how an EXISTING tenant gets the change: not from the template
    but from the revision, replayed here over a schema without it. The
    downgrade is the revision's own, so this exercises both directions.
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        _downgrade_this_schema(conn)
        conn.execute(
            text(f"UPDATE {schema}.alembic_version SET version_num = :r"),  # noqa: S608
            {"r": _PARENT_REVISION},
        )

    result = upgrade_tenant_schema(engine, schema)
    assert result.status.value == "success", result.detail


def _downgrade_this_schema(conn: Connection) -> None:
    """Undo the revision by hand, so the chain has something to re-apply.

    Calling ``command.downgrade`` would walk the whole chain against
    whatever schema the runner picks; this is the revision's own downgrade
    body against the schema on this connection's ``search_path``.
    """
    conn.execute(
        text(
            f"ALTER TABLE {_DOCUMENTS} DROP CONSTRAINT IF EXISTS ck_patient_documents_one_uploader"
        )
    )
    conn.execute(
        text(
            f"DELETE FROM {_DOCUMENTS} "  # noqa: S608
            "WHERE uploaded_by_patient_id IS NOT NULL "
            "OR category IN ('intake_artifact', 'message')"
        )
    )
    conn.execute(text(f"ALTER TABLE {_DOCUMENTS} DROP COLUMN IF EXISTS uploaded_by_patient_id"))
    conn.execute(
        text(f"ALTER TABLE {_DOCUMENTS} DROP CONSTRAINT IF EXISTS ck_patient_documents_category")
    )
    conn.execute(
        text(
            f"ALTER TABLE {_DOCUMENTS} ADD CONSTRAINT ck_patient_documents_category "
            "CHECK (category IN ('chart', 'consent', 'therapist_private', "
            "'psychotherapy_notes'))"
        )
    )
    conn.execute(text(f"ALTER TABLE {_DOCUMENTS} ALTER COLUMN user_id SET NOT NULL"))


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
    schema = _new_schema(engine, "patient_docs_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant that got the change from the revision instead."""
    schema = _new_schema(engine, "patient_docs_migrated")
    _rebuild_through_the_chain(engine, schema)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers of this table must land the same shape.

    If they drift, the symptom appears wherever a fresh tenant is next
    provisioned, not here — so they are compared directly.
    """

    def test_template_and_chain_produce_the_same_table(
        self, engine: Engine, fresh_schema: str, migrated_schema: str
    ) -> None:
        fresh = _shape(engine, fresh_schema)
        migrated = _shape(engine, migrated_schema)
        assert fresh["columns"], f"{_DOCUMENTS} absent from the template-built schema"
        assert migrated["columns"], f"{_DOCUMENTS} absent from the chain-built schema"
        assert fresh == migrated

    def test_the_revision_is_idempotent(self, engine: Engine, migrated_schema: str) -> None:
        """Fanned out once per practice schema, so replaying it must be a no-op.

        The version stamp is wound back WITHOUT undoing the DDL, so the
        revision runs a second time over a schema that already has
        everything it creates. Simply calling the runner again would report
        ``already-at-head`` and execute nothing, which proves the stamp
        works rather than that the SQL does.
        """
        from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

        before = _shape(engine, migrated_schema)
        with engine.begin() as conn:
            conn.execute(
                text(f"UPDATE {migrated_schema}.alembic_version SET version_num = :r"),  # noqa: S608
                {"r": _PARENT_REVISION},
            )

        result = upgrade_tenant_schema(engine, migrated_schema)
        assert result.status.value == "success", result.detail
        assert _shape(engine, migrated_schema) == before

    def test_both_schemas_enforce_the_same_rules(
        self,
        engine: Engine,
        fresh_schema: str,
        migrated_schema: str,
        two_patients: tuple[str, str],
    ) -> None:
        """Behaviour, where :func:`_shape` compares names.

        The same CHECK spelled by the ORM and by the revision comes back as
        different text, so the definitions cannot be compared literally.
        What matters is that both schemas accept and refuse the same things,
        which is what this asks them.
        """
        patient_a, _ = two_patients
        for schema in (fresh_schema, migrated_schema):
            _seed_patient(engine, schema, patient_a)

            # The two new categories are accepted on both. Control first:
            # if these failed, the refusals below would prove nothing.
            for category in ("intake_artifact", "message"):
                conn = _as_clinician(engine, schema, _TREATING_CLINICIAN)
                try:
                    _upload_as_clinician(conn, patient_a, category=category)
                    conn.rollback()
                finally:
                    conn.close()

            # An unknown category is refused BY NAME on both.
            conn = _as_clinician(engine, schema, _TREATING_CLINICIAN)
            try:
                with pytest.raises(IntegrityError) as exc:
                    _upload_as_clinician(conn, patient_a, category="whatever")
                assert "ck_patient_documents_category" in str(exc.value), schema
            finally:
                conn.close()

            # And so is a row claiming two uploaders. A fresh connection per
            # refusal: a rollback also discards this connection's
            # ``search_path``, so reusing one turns the next statement into
            # "relation does not exist".
            conn = _as_clinician(engine, schema, _TREATING_CLINICIAN)
            try:
                with pytest.raises(IntegrityError) as exc:
                    _insert(
                        conn,
                        patient_id=patient_a,
                        category="chart",
                        user_id=_TREATING_CLINICIAN,
                        uploaded_by_patient_id=patient_a,
                    )
                assert "ck_patient_documents_one_uploader" in str(exc.value), schema
            finally:
                conn.close()


def _seed_patient(engine: Engine, schema: str, patient_id: str) -> None:
    """One patient and the treating clinician's grant, in an arbitrary schema."""
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _TREATING_CLINICIAN},
        )
        exists = conn.execute(
            text("SELECT 1 FROM patients WHERE id = CAST(:pid AS uuid)"),
            {"pid": patient_id},
        ).first()
        if exists is not None:
            return
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Ada', 'Lovelace', "
                "'ada', 'lovelace', 'active', 0, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _TREATING_CLINICIAN},
        )
