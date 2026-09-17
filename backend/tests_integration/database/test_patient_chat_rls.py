# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Two-patient isolation on the chat tables, against a real provisioned schema.

The chat tables are the first patient-writable tables where the row test is
not a plain column: ``chat_conversations`` carries the patient's id whether
the patient or a clinician started the conversation, and ``chat_messages``
carries no owning column at all. So the policies under test are bespoke,
and every claim the route layer makes about them is asserted here at the
database, with the ``pablo`` role the integration conftest creates
``NOSUPERUSER NOBYPASSRLS``.

Three principals, four conversations:

* a clinician's conversation ABOUT patient A;
* patient A's own conversation;
* patient B's own conversation;
* and, per test, whatever a patient tries to forge.

Every invisibility assertion follows a visibility control on the same
connection, so nothing here passes on an empty table.
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

    from sqlalchemy.engine import Connection, Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_CLINICIAN = "6c2f9a41-8b3d-5e7a-9f04-1d8c3b6e2a95"

_INSERT_CONVERSATION = text(
    "INSERT INTO chat_conversations (id, patient_id, owner_user_id, title, "
    "caller_system_prompt, caller_feature_key, created_at) "
    "VALUES (CAST(:cid AS uuid), CAST(:pid AS uuid), CAST(:owner AS uuid), :title, "
    "'prompt', :feature, now())"
)
_INSERT_MESSAGE = text(
    "INSERT INTO chat_messages (id, conversation_id, sequence, role, content, created_at) "
    "VALUES (CAST(:mid AS uuid), CAST(:cid AS uuid), :seq, 'user', :content, now())"
)


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

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_pchat_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _as_clinician(conn: Connection, schema: str) -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})


def _as_patient(engine: Engine, schema: str, patient_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("SELECT set_config('app.current_patient_id', :p, false)"), {"p": patient_id})
    return conn


@pytest.fixture(scope="module")
def two_patients(engine: Engine, tenant_schema: str) -> tuple[str, str]:
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    with engine.begin() as conn:
        _as_clinician(conn, tenant_schema)
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
                {"pid": pid, "u": _CLINICIAN},
            )
    return patient_a, patient_b


@pytest.fixture(scope="module")
def conversations(
    engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
) -> dict[str, str]:
    """``about_a`` (clinician-owned), ``own_a`` and ``own_b`` (patient-owned).

    The two patient-owned rows are inserted AS the patient, which is the
    write arm's positive case and the control for every forgery test.
    """
    patient_a, patient_b = two_patients
    ids = {"about_a": str(uuid.uuid4()), "own_a": str(uuid.uuid4()), "own_b": str(uuid.uuid4())}

    with engine.begin() as conn:
        _as_clinician(conn, tenant_schema)
        conn.execute(
            _INSERT_CONVERSATION,
            {
                "cid": ids["about_a"],
                "pid": patient_a,
                "owner": _CLINICIAN,
                "title": "Chat about Ada",
                "feature": "chart_qa",
            },
        )
        conn.execute(
            _INSERT_MESSAGE,
            {"mid": str(uuid.uuid4()), "cid": ids["about_a"], "seq": 1, "content": "chart q"},
        )

    for key, patient_id in (("own_a", patient_a), ("own_b", patient_b)):
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            conn.execute(
                _INSERT_CONVERSATION,
                {
                    "cid": ids[key],
                    "pid": patient_id,
                    "owner": None,
                    "title": "Rough week",
                    "feature": "patient_chat",
                },
            )
            conn.execute(
                _INSERT_MESSAGE,
                {"mid": str(uuid.uuid4()), "cid": ids[key], "seq": 1, "content": "my words"},
            )
            conn.commit()
        finally:
            conn.close()
    return ids


def _visible_conversations(conn: Connection) -> set[str]:
    return {str(r) for r in conn.execute(text("SELECT id FROM chat_conversations")).scalars()}


def _visible_messages(conn: Connection) -> set[str]:
    return {
        str(r) for r in conn.execute(text("SELECT conversation_id FROM chat_messages")).scalars()
    }


class TestTheArmsExist:
    @pytest.mark.parametrize("table", ["chat_conversations", "chat_messages"])
    def test_all_four_patient_policies_are_provisioned(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT policyname, cmd FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = :t"
                ),
                {"s": tenant_schema, "t": table},
            ).all()
        by_name = {r[0]: r[1] for r in rows}
        assert by_name.get("rls_patient_self_read") == "SELECT", by_name
        assert by_name.get("rls_patient_self_write") == "UPDATE", by_name
        assert by_name.get("rls_patient_self_insert") == "INSERT", by_name
        assert by_name.get("rls_patient_self_delete") == "DELETE", by_name

    def test_outcome_measures_did_not_grow_a_delete_arm(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            names = (
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = 'outcome_measures'"
                    ),
                    {"s": tenant_schema},
                )
                .scalars()
                .all()
            )
        assert "rls_patient_self_delete" not in names


class TestReads:
    def test_a_sees_exactly_their_own_conversation(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str], conversations
    ) -> None:
        """Visibility control, and the two exclusions in one assertion:
        not B's, and not the clinician's conversation about A."""
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            assert _visible_conversations(conn) == {conversations["own_a"]}
            assert _visible_messages(conn) == {conversations["own_a"]}
        finally:
            conn.close()

    def test_b_sees_exactly_their_own(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str], conversations
    ) -> None:
        _, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            assert _visible_conversations(conn) == {conversations["own_b"]}
        finally:
            conn.close()

    def test_naming_the_clinicians_conversation_outright_returns_nothing(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str], conversations
    ) -> None:
        """The IDOR move against the harder case: same patient_id, an owner."""
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            own = conn.execute(
                text("SELECT id FROM chat_conversations WHERE id = CAST(:c AS uuid)"),
                {"c": conversations["own_a"]},
            ).scalar()
            assert str(own) == conversations["own_a"]

            about = conn.execute(
                text("SELECT id FROM chat_conversations WHERE id = CAST(:c AS uuid)"),
                {"c": conversations["about_a"]},
            ).scalar()
            assert about is None, "a patient read the clinician's conversation about them"

            about_messages = conn.execute(
                text("SELECT count(*) FROM chat_messages WHERE conversation_id = CAST(:c AS uuid)"),
                {"c": conversations["about_a"]},
            ).scalar_one()
            assert about_messages == 0
        finally:
            conn.close()

    def test_the_clinician_still_sees_their_own_conversation(
        self, engine: Engine, tenant_schema: str, conversations
    ) -> None:
        """The patient arm is additive; the clinician policy is untouched."""
        with engine.connect() as conn:
            _as_clinician(conn, tenant_schema)
            assert conversations["about_a"] in _visible_conversations(conn)


class TestWrites:
    def test_a_cannot_start_a_conversation_for_b(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        patient_a, patient_b = two_patients
        forged = str(uuid.uuid4())
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as excinfo:
                conn.execute(
                    _INSERT_CONVERSATION,
                    {
                        "cid": forged,
                        "pid": patient_b,
                        "owner": None,
                        "title": "forged",
                        "feature": "patient_chat",
                    },
                )
            assert "row-level security" in str(excinfo.value).lower()
        finally:
            conn.rollback()
            conn.close()

    def test_a_cannot_attribute_a_conversation_to_a_clinician(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """Own patient_id, but an owner: the insert arm requires NULL.

        Without this a patient could plant a conversation on the clinician
        surface, where it would read as something the clinician started.
        """
        patient_a, _ = two_patients
        forged = str(uuid.uuid4())
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as excinfo:
                conn.execute(
                    _INSERT_CONVERSATION,
                    {
                        "cid": forged,
                        "pid": patient_a,
                        "owner": _CLINICIAN,
                        "title": "planted",
                        "feature": "chart_qa",
                    },
                )
            assert "row-level security" in str(excinfo.value).lower()
        finally:
            conn.rollback()
            conn.close()

    @pytest.mark.parametrize("target", ["own_b", "about_a"])
    def test_a_cannot_append_a_message_to_a_conversation_that_is_not_theirs(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        conversations,
        target: str,
    ) -> None:
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as excinfo:
                conn.execute(
                    _INSERT_MESSAGE,
                    {
                        "mid": str(uuid.uuid4()),
                        "cid": conversations[target],
                        "seq": 99,
                        "content": "injected",
                    },
                )
            assert "row-level security" in str(excinfo.value).lower()
        finally:
            conn.rollback()
            conn.close()

    def test_a_cannot_rename_bs_conversation(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str], conversations
    ) -> None:
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            result = conn.execute(
                text(
                    "UPDATE chat_conversations SET title = 'tampered' WHERE id = CAST(:c AS uuid)"
                ),
                {"c": conversations["own_b"]},
            )
            assert result.rowcount == 0
            conn.commit()
        finally:
            conn.close()

        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            title = conn.execute(
                text("SELECT title FROM chat_conversations WHERE id = CAST(:c AS uuid)"),
                {"c": conversations["own_b"]},
            ).scalar()
            assert title == "Rough week"
        finally:
            conn.close()


class TestDelete:
    """The arm this revision adds. Without it a purge is a silent no-op."""

    def test_a_cannot_delete_bs_conversation_or_its_messages(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str], conversations
    ) -> None:
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            messages = conn.execute(
                text("DELETE FROM chat_messages WHERE conversation_id = CAST(:c AS uuid)"),
                {"c": conversations["own_b"]},
            )
            rows = conn.execute(
                text("DELETE FROM chat_conversations WHERE id = CAST(:c AS uuid)"),
                {"c": conversations["own_b"]},
            )
            assert messages.rowcount == 0
            assert rows.rowcount == 0
            conn.commit()
        finally:
            conn.close()

        conn = _as_patient(engine, tenant_schema, patient_b)
        try:
            assert _visible_conversations(conn) == {conversations["own_b"]}
            assert _visible_messages(conn) == {conversations["own_b"]}
        finally:
            conn.close()

    def test_a_cannot_delete_the_clinicians_conversation_about_them(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str], conversations
    ) -> None:
        patient_a, _ = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            result = conn.execute(
                text("DELETE FROM chat_conversations WHERE id = CAST(:c AS uuid)"),
                {"c": conversations["about_a"]},
            )
            assert result.rowcount == 0
            conn.commit()
        finally:
            conn.close()
        with engine.connect() as conn:
            _as_clinician(conn, tenant_schema)
            assert conversations["about_a"] in _visible_conversations(conn)

    def test_a_can_purge_their_own_conversation(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """The positive case, on a conversation created for this test so the
        module-scoped fixtures stay intact for everything else."""
        patient_a, _ = two_patients
        cid = str(uuid.uuid4())
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            conn.execute(
                _INSERT_CONVERSATION,
                {
                    "cid": cid,
                    "pid": patient_a,
                    "owner": None,
                    "title": "to purge",
                    "feature": "patient_chat",
                },
            )
            conn.execute(
                _INSERT_MESSAGE,
                {"mid": str(uuid.uuid4()), "cid": cid, "seq": 1, "content": "gone soon"},
            )
            conn.commit()
            assert cid in _visible_conversations(conn)

            messages = conn.execute(
                text("DELETE FROM chat_messages WHERE conversation_id = CAST(:c AS uuid)"),
                {"c": cid},
            )
            rows = conn.execute(
                text("DELETE FROM chat_conversations WHERE id = CAST(:c AS uuid)"), {"c": cid}
            )
            assert messages.rowcount == 1
            assert rows.rowcount == 1
            conn.commit()
        finally:
            conn.close()

        # Gone for everyone, checked as the clinician who can see the practice.
        with engine.connect() as conn:
            _as_clinician(conn, tenant_schema)
            assert cid not in _visible_conversations(conn)
