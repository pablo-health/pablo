# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for files on a secure message.

``patient_message_attachments`` is registered patient-readable AND
patient-writable: attaching is a patient INSERT, and reading a thread back
reads the links. That is a wide grant, so the policies it produces are
proved here against a schema built by the real ``create_practice_schema``
(so the policies under test are the ones that ship) and connected as the
``pablo`` role, which the integration conftest creates ``NOSUPERUSER
NOBYPASSRLS`` exactly as production has it.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion is
preceded by a visibility control on the same connection, so no assertion
can pass because the table was empty, the schema was wrong or the GUC was
never armed. The role's RLS-bypass bits are asserted up front for the same
reason.

Three things beyond isolation are proved here because only a real database
can prove them: the two composite foreign keys, which are what stop a link
from naming one patient's message and another patient's file; and the
unique constraint on ``document_id``, which is the "already sent" rule the
send routes answer with a 422.

The last section compares the two producers of the table. A freshly
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

_ATTACHMENTS = "patient_message_attachments"
# The clinician who created both patients, and so holds a
# ``patient_clinicians`` grant on each.
_TREATING_CLINICIAN = "3f81b207-6d4a-5c19-8e72-0a5d9c14b8e3"
# A clinician in the same practice with no grant on either patient.
_STRANGER_CLINICIAN = "9a4e62d8-1f07-5b35-a6c8-7e20d4f91b56"

# The revision this one follows. Rolling a schema back to it and forward
# again replays exactly the revision under test.
_PARENT_REVISION = "c5e1a9f0d248"


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
    schema = _new_schema(engine, "msgattach")
    yield schema
    _drop_schema(engine, schema)


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


def _open_thread(conn: Connection, patient_id: str) -> str:
    thread_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO patient_message_threads "
            "(id, patient_id, subject, status, created_at, last_message_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'seed', 'open', :now, :now)"
        ),
        {"id": thread_id, "pid": patient_id, "now": _now()},
    )
    return thread_id


def _send(conn: Connection, patient_id: str, thread_id: str, *, sender: str = "patient") -> str:
    message_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO patient_messages "
            "(id, thread_id, patient_id, sender, body, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:pid AS uuid), "
            ":sender, 'seed body', :now)"
        ),
        {
            "id": message_id,
            "tid": thread_id,
            "pid": patient_id,
            "sender": sender,
            "now": _now(),
        },
    )
    return message_id


def _upload(conn: Connection, patient_id: str, *, uploaded_by_patient: bool = True) -> str:
    """A finalized ``message``-category document on this patient's chart."""
    document_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO patient_documents "
            "(id, patient_id, user_id, uploaded_by_patient_id, filename, mime_type, "
            " gcs_path, size_bytes, category, created_at, finalized_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:uid AS uuid), "
            "CAST(:upid AS uuid), 'seed.png', 'image/png', :path, 1024, 'message', "
            ":now, :now)"
        ),
        {
            "id": document_id,
            "pid": patient_id,
            "uid": None if uploaded_by_patient else _TREATING_CLINICIAN,
            "upid": patient_id if uploaded_by_patient else None,
            "path": f"seed/{document_id}.png",
            "now": _now(),
        },
    )
    return document_id


def _attach(conn: Connection, patient_id: str, message_id: str, document_id: str) -> str:
    link_id = str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {_ATTACHMENTS} "  # noqa: S608 — module constant, no caller input
            "(id, message_id, document_id, patient_id, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:mid AS uuid), CAST(:did AS uuid), "
            "CAST(:pid AS uuid), :now)"
        ),
        {
            "id": link_id,
            "mid": message_id,
            "did": document_id,
            "pid": patient_id,
            "now": _now(),
        },
    )
    return link_id


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _visible_links(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_ATTACHMENTS}")).scalars().all()  # noqa: S608
    return set(rows)


@pytest.fixture(scope="module")
def seeded(engine: Engine, tenant_schema: str, two_patients: tuple[str, str]) -> dict[str, str]:
    """One attached file per patient, each written BY that patient.

    Deliberately not seeded clinician-side. Every insert runs on a
    connection with only ``app.current_patient_id`` armed, so the fixture
    exercises the write arm the registration grants — if that arm were
    missing, FORCE ROW LEVEL SECURITY would refuse the INSERT and this
    fixture would error rather than quietly leaving an empty table for the
    read tests to pass against.
    """
    seeded: dict[str, str] = {}
    for label, patient_id in zip(("a", "b"), two_patients, strict=True):
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            thread_id = _open_thread(conn, patient_id)
            message_id = _send(conn, patient_id, thread_id)
            document_id = _upload(conn, patient_id)
            link_id = _attach(conn, patient_id, message_id, document_id)
            conn.commit()
        finally:
            conn.close()
        seeded[f"thread_{label}"] = thread_id
        seeded[f"message_{label}"] = message_id
        seeded[f"document_{label}"] = document_id
        seeded[f"link_{label}"] = link_id
    return seeded


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
                    {"s": tenant_schema, "t": _ATTACHMENTS},
                )
                .mappings()
                .first()
            )
        assert row is not None, (
            f"{_ATTACHMENTS} missing from a freshly-provisioned tenant — the "
            "migration or the tenant_template.sql regen did not ship"
        )
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["policy_count"] >= 1, "RLS forced with no policy is a silent deny-all"

    def test_it_needed_no_bespoke_policy(self, engine: Engine, tenant_schema: str) -> None:
        """The denormalized ``patient_id`` is what avoids one.

        The same four arms ``patient_messages`` gets, for the same reason:
        read, insert and update for the patient, ``has_patient_access`` for
        the clinician. A fifth name here would mean the registration and
        the models had quietly diverged.
        """
        with engine.connect() as conn:
            names = set(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": _ATTACHMENTS},
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
    def test_each_patient_can_attach_a_file_and_read_it_back(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """The write control. Without it the refusals below prove nothing.

        Reaching this assertion at all already means the insert arm admitted
        a patient linking their own message to their own document, because
        that is what the fixture did. This reads it back to prove the read
        arm agrees.
        """
        for label, patient_id in zip(("a", "b"), two_patients, strict=True):
            conn = _as_patient(engine, tenant_schema, patient_id)
            try:
                assert _visible_links(conn) == {seeded[f"link_{label}"]}
            finally:
                conn.close()

    def test_a_cannot_see_bs_link(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: A sees their own, so the table is not simply empty.
            assert seeded["link_a"] in _visible_links(conn)
            assert seeded["link_b"] not in _visible_links(conn)

            # The IDOR move: armed as A, name B's rows outright.
            named = conn.execute(
                text(
                    f"SELECT id FROM {_ATTACHMENTS} "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).scalars()
            assert list(named) == []

            by_document = conn.execute(
                text(
                    f"SELECT id FROM {_ATTACHMENTS} "  # noqa: S608
                    "WHERE document_id = CAST(:d AS uuid)"
                ),
                {"d": seeded["document_b"]},
            ).scalars()
            assert list(by_document) == [], "naming B's document must not find B's link"
        finally:
            conn.close()

    def test_a_cannot_attach_to_bs_message(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """WITH CHECK on the insert arm: A cannot file a link as B."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _attach(conn, patient_b, seeded["message_b"], seeded["document_b"])
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_attach_bs_document_to_as_own_message(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """Claiming A's own ``patient_id`` is what the foreign key catches.

        The row passes the policy — it says ``patient_id`` is A — so the
        only thing left to refuse it is the composite key into
        ``patient_documents``, which has no ``(B's document, A)`` pair.
        """
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: the same insert with A's own document succeeds.
            own_document = _upload(conn, patient_a)
            _attach(conn, patient_a, seeded["message_a"], own_document)

            with pytest.raises(IntegrityError):
                _attach(conn, patient_a, seeded["message_a"], seeded["document_b"])
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("seeded")
    def test_a_cannot_update_bs_link(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
    ) -> None:
        """Invisible to a write as well as to a read: rowcount 0, no error."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            own = conn.execute(
                text(
                    f"UPDATE {_ATTACHMENTS} SET created_at = now() "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_a},
            ).rowcount
            assert own >= 1
            foreign = conn.execute(
                text(
                    f"UPDATE {_ATTACHMENTS} SET created_at = now() "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).rowcount
            assert foreign == 0
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("seeded")
    def test_no_principal_armed_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Fail-closed. The rows exist — the fixture put them there."""
        conn = _unarmed(engine, tenant_schema)
        try:
            assert _visible_links(conn) == set()
        finally:
            conn.close()


class TestClinicianAccess:
    def test_treating_clinician_sees_and_attaches(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """``has_patient_access`` reaches the rows a grant covers, both ways."""
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert {seeded["link_a"], seeded["link_b"]} <= _visible_links(conn)
            reply_id = _send(conn, patient_a, seeded["thread_a"], sender="clinician")
            document_id = _upload(conn, patient_a, uploaded_by_patient=False)
            link_id = _attach(conn, patient_a, reply_id, document_id)
            assert link_id in _visible_links(conn)
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.usefixtures("seeded")
    def test_stranger_clinician_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Same practice, no grant on either patient. The control is above."""
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            assert _visible_links(conn) == set()
        finally:
            conn.close()

    def test_stranger_clinician_cannot_attach(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _attach(conn, patient_a, seeded["message_a"], seeded["document_a"])
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()


class TestIntegrityConstraints:
    """What the database refuses regardless of who is asking.

    Run as the treating clinician, who has a grant on BOTH patients — so
    row security admits every write below and the only thing left to refuse
    one is the constraint under test.
    """

    def test_a_link_cannot_claim_a_patient_its_message_does_not_have(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """The composite key into ``patient_messages``."""
        patient_a, patient_b = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # Control: the same insert with the matching patient succeeds.
            document_id = _upload(conn, patient_a, uploaded_by_patient=False)
            _attach(conn, patient_a, seeded["message_a"], document_id)

            other = _upload(conn, patient_b, uploaded_by_patient=False)
            with pytest.raises(IntegrityError):
                _attach(conn, patient_b, seeded["message_a"], other)
            conn.rollback()
        finally:
            conn.close()

    def test_a_file_rides_on_one_message_or_none(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """The unique constraint behind the routes' "already sent" 422."""
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            document_id = _upload(conn, patient_a, uploaded_by_patient=False)
            second_message = _send(conn, patient_a, seeded["thread_a"], sender="clinician")
            _attach(conn, patient_a, seeded["message_a"], document_id)

            with pytest.raises(IntegrityError) as exc:
                _attach(conn, patient_a, second_message, document_id)
            assert "uq_patient_message_attachments_document" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_deleting_a_message_takes_its_links_and_leaves_the_document(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """CASCADE one way, RESTRICT the other.

        A removed message leaves no dangling link; the file stays on the
        chart, which is where it has belonged since it was uploaded.
        """
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            message_id = _send(conn, patient_a, seeded["thread_a"], sender="clinician")
            document_id = _upload(conn, patient_a, uploaded_by_patient=False)
            link_id = _attach(conn, patient_a, message_id, document_id)
            assert link_id in _visible_links(conn)

            conn.execute(
                text("DELETE FROM patient_messages WHERE id = CAST(:id AS uuid)"),
                {"id": message_id},
            )
            assert link_id not in _visible_links(conn)
            still_there = conn.execute(
                text("SELECT count(*) FROM patient_documents WHERE id = CAST(:id AS uuid)"),
                {"id": document_id},
            ).scalar()
            assert still_there == 1
            conn.rollback()
        finally:
            conn.close()

    def test_an_attached_document_cannot_be_deleted_out_from_under_it(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        seeded: dict[str, str],
    ) -> None:
        """RESTRICT: correspondence does not lose its enclosure to a hard delete.

        Soft delete is what the product does to a document, and it is
        untouched — this refuses only the row-level DELETE.
        """
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # Control: an unattached document deletes.
            loose = _upload(conn, patient_a, uploaded_by_patient=False)
            deleted = conn.execute(
                text("DELETE FROM patient_documents WHERE id = CAST(:id AS uuid)"),
                {"id": loose},
            ).rowcount
            assert deleted == 1

            with pytest.raises(IntegrityError):
                conn.execute(
                    text("DELETE FROM patient_documents WHERE id = CAST(:id AS uuid)"),
                    {"id": seeded["document_a"]},
                )
            conn.rollback()
        finally:
            conn.close()


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
    """Drop the table, roll the schema back one revision, migrate forward.

    This is how an existing tenant gets it: not from the template but from
    the revision, replayed here over a schema that does not have it. The
    two parent constraints the revision also adds are left in place on
    purpose — the revision's guards must treat an already-present
    constraint as nothing to do, which is what a fan-out over a schema that
    has seen the revision before actually meets.
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_ATTACHMENTS} CASCADE"))
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
    schema = _new_schema(engine, "msgattach_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant that got the table from the revision instead."""
    schema = _new_schema(engine, "msgattach_migrated")
    _rebuild_through_the_chain(engine, schema)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers of this table must land the same shape.

    If they drift, the symptom appears wherever a fresh tenant is next
    provisioned, not here — so they are compared directly.
    """

    @pytest.mark.parametrize("table", [_ATTACHMENTS, "patient_messages", "patient_documents"])
    def test_template_and_chain_produce_the_same_table(
        self, engine: Engine, fresh_schema: str, migrated_schema: str, table: str
    ) -> None:
        """The two parents are compared too: the revision alters them as well."""
        fresh = _shape(engine, fresh_schema, table)
        migrated = _shape(engine, migrated_schema, table)
        assert fresh["columns"], f"{table} absent from the template-built schema"
        assert migrated["columns"], f"{table} absent from the chain-built schema"
        assert fresh == migrated

    def test_the_revision_is_idempotent(self, engine: Engine, migrated_schema: str) -> None:
        """Fanned out once per practice schema, so replaying it must be a no-op."""
        before = _shape(engine, migrated_schema, _ATTACHMENTS)
        _rebuild_through_the_chain(engine, migrated_schema)
        _reapply_policies(engine, migrated_schema)
        assert _shape(engine, migrated_schema, _ATTACHMENTS) == before
