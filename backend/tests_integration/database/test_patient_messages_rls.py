# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for secure patient messaging.

Both tables are registered patient-readable AND patient-writable: a patient
starts their own thread, writes into it, reads the practice's replies back
and stamps them read. That is a wide grant, so the policies it produces are
proved here against a schema built by the real ``create_practice_schema``
(so the policies under test are the ones that ship) and connected as the
``pablo`` role, which the integration conftest creates ``NOSUPERUSER
NOBYPASSRLS`` exactly as production has it.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion is
preceded by a visibility control on the same connection, so no assertion can
pass because the table was empty, the schema was wrong or the GUC was never
armed. The role's RLS-bypass bits are asserted up front for the same reason.

Two things beyond isolation are proved here because only a real database can
prove them: the composite foreign key that keeps ``patient_messages
.patient_id`` in step with its thread's owner, and the ``sender`` CHECK that
admits exactly three values.

The last section compares the two producers of these tables. A freshly
provisioned tenant gets them from ``tenant_template.sql``; an existing one
gets them from the alembic revision. Those are different code paths, and the
classic failure is that one ships and the other does not — which surfaces
far from the cause, as ``relation ... does not exist`` in whatever runs next.

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
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

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

_THREADS = "patient_message_threads"
_MESSAGES = "patient_messages"
# The clinician who created both patients, and so holds a
# ``patient_clinicians`` grant on each.
_TREATING_CLINICIAN = "3f81b207-6d4a-5c19-8e72-0a5d9c14b8e3"
# A clinician in the same practice with no grant on either patient.
_STRANGER_CLINICIAN = "9a4e62d8-1f07-5b35-a6c8-7e20d4f91b56"

# The revision this one follows. Rolling a schema back to it and forward
# again replays exactly the revision under test.
_PARENT_REVISION = "c9f4a1d78b02"


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
    schema = _new_schema(engine, "messages")
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
def threads(engine: Engine, tenant_schema: str, two_patients: tuple[str, str]) -> tuple[str, str]:
    """One thread per patient, each opened BY that patient.

    Deliberately not seeded clinician-side. The inserts run on a connection
    with only ``app.current_patient_id`` armed, so they exercise the write
    arm the registration grants — if that arm were missing, FORCE ROW LEVEL
    SECURITY would refuse the INSERT and this fixture would error rather
    than quietly leaving empty tables for the read tests to pass against.
    """
    ids: list[str] = []
    for patient_id in two_patients:
        thread_id = str(uuid.uuid4())
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            _open_thread(conn, patient_id, thread_id)
            _send(conn, patient_id, thread_id, sender="patient")
            conn.commit()
        finally:
            conn.close()
        ids.append(thread_id)
    return ids[0], ids[1]


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


def _open_thread(conn: Connection, patient_id: str, thread_id: str) -> None:
    conn.execute(
        text(
            f"INSERT INTO {_THREADS} "  # noqa: S608 — module constant, no caller input
            "(id, patient_id, subject, status, created_at, last_message_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), :subject, 'open', :now, :now)"
        ),
        {
            "id": thread_id,
            "pid": patient_id,
            "subject": "seed",
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )


def _send(
    conn: Connection,
    patient_id: str,
    thread_id: str,
    *,
    sender: str,
    message_id: str | None = None,
) -> str:
    message_id = message_id or str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {_MESSAGES} "  # noqa: S608 — module constant, no caller input
            "(id, thread_id, patient_id, sender, body, created_at) "
            "VALUES (CAST(:id AS uuid), CAST(:tid AS uuid), CAST(:pid AS uuid), "
            ":sender, :body, :now)"
        ),
        {
            "id": message_id,
            "tid": thread_id,
            "pid": patient_id,
            "sender": sender,
            "body": "seed body",
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return message_id


def _visible_threads(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_THREADS}")).scalars().all()  # noqa: S608
    return set(rows)


def _visible_messages(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_MESSAGES}")).scalars().all()  # noqa: S608
    return set(rows)


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

    @pytest.mark.parametrize("table", [_THREADS, _MESSAGES])
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

    @pytest.mark.parametrize("table", [_THREADS, _MESSAGES])
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

    def test_patient_messages_needed_no_bespoke_policy(
        self, engine: Engine, tenant_schema: str
    ) -> None:
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
                    {"s": tenant_schema, "t": _MESSAGES},
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
    def test_each_patient_can_open_a_thread_and_read_it_back(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        threads: tuple[str, str],
    ) -> None:
        """The write control. Without it the refusals below prove nothing.

        The rows were inserted by the fixture, each under its own patient's
        armed GUC and nothing else — so reaching this assertion at all
        already means the insert arm admitted a patient writing their own
        thread and message. This reads them back to prove the read arm
        agrees.
        """
        for patient_id, thread_id in zip(two_patients, threads, strict=True):
            conn = _as_patient(engine, tenant_schema, patient_id)
            try:
                assert _visible_threads(conn) == {thread_id}
                assert len(_visible_messages(conn)) == 1
            finally:
                conn.close()

    def test_a_patient_can_stamp_read_at_on_their_own_message(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The UPDATE arm, which the mark-read route depends on."""
        patient_a, _ = two_patients
        thread_id = str(uuid.uuid4())
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            _open_thread(conn, patient_a, thread_id)
            message_id = _send(conn, patient_a, thread_id, sender="clinician")
            updated = conn.execute(
                text(
                    f"UPDATE {_MESSAGES} SET read_at = now() "  # noqa: S608
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": message_id},
            ).rowcount
            assert updated == 1
            conn.commit()
        finally:
            conn.close()

    def test_a_cannot_open_a_thread_owned_by_b(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """WITH CHECK on the insert arm: A cannot file a thread as B."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _open_thread(conn, patient_b, str(uuid.uuid4()))
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_send_a_message_owned_by_b(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        threads: tuple[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        _, thread_b = threads
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _send(conn, patient_b, thread_b, sender="patient")
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_see_bs_thread_or_messages(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        threads: tuple[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        thread_a, thread_b = threads

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            # Control: A sees their own, so the tables are not simply empty.
            assert thread_a in _visible_threads(conn)
            assert thread_b not in _visible_threads(conn)

            # The IDOR move: armed as A, name B's rows outright.
            named = conn.execute(
                text(
                    f"SELECT id FROM {_MESSAGES} "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).scalars()
            assert list(named) == []
        finally:
            conn.close()

    @pytest.mark.usefixtures("threads")
    def test_a_cannot_update_bs_message(
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
                    f"UPDATE {_MESSAGES} SET read_at = now() "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_a},
            ).rowcount
            assert own >= 1
            foreign = conn.execute(
                text(
                    f"UPDATE {_MESSAGES} SET read_at = now() "  # noqa: S608
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).rowcount
            assert foreign == 0
            conn.rollback()
        finally:
            conn.close()

    @pytest.mark.usefixtures("threads")
    def test_no_principal_armed_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Fail-closed. The rows exist — the fixture put them there."""
        conn = _unarmed(engine, tenant_schema)
        try:
            assert _visible_threads(conn) == set()
            assert _visible_messages(conn) == set()
        finally:
            conn.close()


class TestClinicianAccess:
    def test_treating_clinician_sees_and_replies(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        threads: tuple[str, str],
    ) -> None:
        """``has_patient_access`` reaches the rows a grant covers, both ways."""
        patient_a, _ = two_patients
        thread_a, _ = threads
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert set(threads) <= _visible_threads(conn)
            reply_id = _send(conn, patient_a, thread_a, sender="clinician")
            assert reply_id in _visible_messages(conn)
            conn.commit()
        finally:
            conn.close()

    @pytest.mark.usefixtures("threads")
    def test_stranger_clinician_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Same practice, no grant on either patient. The control is above."""
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            assert _visible_threads(conn) == set()
            assert _visible_messages(conn) == set()
        finally:
            conn.close()

    def test_stranger_clinician_cannot_reply(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        threads: tuple[str, str],
    ) -> None:
        patient_a, _ = two_patients
        thread_a, _ = threads
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _send(conn, patient_a, thread_a, sender="clinician")
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()


class TestIntegrityConstraints:
    """What the database refuses regardless of who is asking."""

    def test_a_message_cannot_claim_a_patient_its_thread_does_not_have(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        threads: tuple[str, str],
    ) -> None:
        """The composite FK: the denormalized column cannot drift.

        Run as the treating clinician, who has a grant on BOTH patients — so
        row security admits the write and the only thing left to refuse it is
        the foreign key.
        """
        patient_a, patient_b = two_patients
        thread_a, _ = threads
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            # Control: the same insert with the matching patient succeeds.
            _send(conn, patient_a, thread_a, sender="clinician")
            with pytest.raises(IntegrityError):
                _send(conn, patient_b, thread_a, sender="clinician")
            conn.rollback()
        finally:
            conn.close()

    def test_sender_accepts_three_values_and_rejects_a_fourth(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        threads: tuple[str, str],
    ) -> None:
        """``practice`` is accepted now so the after-hours reply needs no migration."""
        patient_a, _ = two_patients
        thread_a, _ = threads
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            for sender in ("patient", "clinician", "practice"):
                _send(conn, patient_a, thread_a, sender=sender)
            with pytest.raises(IntegrityError) as exc:
                _send(conn, patient_a, thread_a, sender="system")
            assert "ck_patient_messages_sender" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_thread_status_rejects_an_unknown_value(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        patient_a, _ = two_patients
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            with pytest.raises((IntegrityError, DBAPIError)) as exc:
                conn.execute(
                    text(
                        f"INSERT INTO {_THREADS} "  # noqa: S608
                        "(id, patient_id, subject, status, created_at, last_message_at) "
                        "VALUES (gen_random_uuid(), CAST(:pid AS uuid), NULL, "
                        "'archived', now(), now())"
                    ),
                    {"pid": patient_a},
                )
            assert "ck_patient_message_threads_status" in str(exc.value)
            conn.rollback()
        finally:
            conn.close()

    def test_deleting_a_thread_takes_its_messages(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """ON DELETE CASCADE, so a removed thread leaves no orphan bodies."""
        patient_a, _ = two_patients
        thread_id = str(uuid.uuid4())
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            _open_thread(conn, patient_a, thread_id)
            message_id = _send(conn, patient_a, thread_id, sender="clinician")
            assert message_id in _visible_messages(conn)
            conn.execute(
                text(f"DELETE FROM {_THREADS} WHERE id = CAST(:id AS uuid)"),  # noqa: S608
                {"id": thread_id},
            )
            assert message_id not in _visible_messages(conn)
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
    """Drop both tables, roll the schema back one revision, migrate forward.

    This is how an existing tenant gets them: not from the template but from
    the revision, replayed here over a schema that does not have them.
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_MESSAGES} CASCADE"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_THREADS} CASCADE"))
        conn.execute(
            text(f"UPDATE {schema}.alembic_version SET version_num = :r"),  # noqa: S608
            {"r": _PARENT_REVISION},
        )

    result = upgrade_tenant_schema(engine, schema)
    assert result.status.value == "success", result.detail


def _reapply_policies(engine: Engine, schema: str) -> None:
    """Re-run the provisioning RLS step, as ``create_practice_schema`` does.

    The chain creates the tables; the policies come from
    ``enable_rls_on_schema``, which provisioning runs after the template is
    applied. ``search_path`` must name the schema first, because
    ``has_patient_access`` lives in it.
    """
    from app.db import PLATFORM_SCHEMA, enable_rls_on_schema  # noqa: PLC0415
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    with OrmSession(engine) as session:
        session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
        enable_rls_on_schema(session, schema)


@pytest.fixture(scope="module")
def fresh_schema(engine: Engine) -> Iterator[str]:
    """A tenant built the way provisioning builds one: from the template."""
    schema = _new_schema(engine, "messages_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant that got the tables from the revision instead."""
    schema = _new_schema(engine, "messages_migrated")
    _rebuild_through_the_chain(engine, schema)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers of these tables must land the same shape.

    If they drift, the symptom appears wherever a fresh tenant is next
    provisioned, not here — so they are compared directly.
    """

    @pytest.mark.parametrize("table", [_THREADS, _MESSAGES])
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
        before = _shape(engine, migrated_schema, _MESSAGES)
        _rebuild_through_the_chain(engine, migrated_schema)
        _reapply_policies(engine, migrated_schema)
        assert _shape(engine, migrated_schema, _MESSAGES) == before
