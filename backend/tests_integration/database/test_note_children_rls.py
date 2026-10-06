# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for a note's signed versions, addenda and dictations.

``note_signatures`` holds the body of a note as it was signed,
``note_addenda`` what a clinician added after, and ``session_dictations`` what
they dictated about the session afterwards; all are readable exactly when
their note is (``rls_note_child_access``). So the same principals as the
restricted-note proof, one chart:

* the **author**, who signed both a progress note and a psychotherapy note on
  patient A and added to each;
* a **co-treater** with a grant on A, who reads the progress note's versions
  and addenda and must not read the psychotherapy note's;
* an **outsider** with no grant, who reads neither;
* the **patient** principal, who reads none of it.

Every invisibility assertion is preceded by a visibility control on the same
connection, so none can pass on an empty table or an unarmed GUC.

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

_CHILDREN = ("note_signatures", "note_addenda", "session_dictations")

_AUTHOR = "1b7e4c2a-6d3f-5a80-9c21-7e4d2f8a6b13"
_CO_TREATER = "8d2f6a19-3c5e-5b74-a0d8-2f9c4e7b1a65"
_OUTSIDER = "4e9a1c73-8b2d-5f06-b3e7-6a1d9c2f8e47"


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

    schema = f"practice_test_note_children_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _connect(engine: Engine, schema: str, *, user_id: str | None = None) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(text("RESET app.current_patient_id"))
    if user_id is not None:
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})
    return conn


@pytest.fixture(scope="module")
def patient(engine: Engine, tenant_schema: str) -> str:
    """One patient, granted to the author and the co-treater."""
    patient_id = str(uuid.uuid4())
    conn = _connect(engine, tenant_schema, user_id=_AUTHOR)
    try:
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
        for user_id in (_AUTHOR, _CO_TREATER):
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id}
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :granted_by)"
                ),
                {"pid": patient_id, "u": user_id, "granted_by": _AUTHOR},
            )
        conn.commit()
    finally:
        conn.close()
    return patient_id


@pytest.fixture(scope="module")
def notes(engine: Engine, tenant_schema: str, patient: str) -> dict[str, str]:
    """A signed progress note and a signed psychotherapy note, each with an addendum.

    Each also carries a dictation on a session of its own. A psychotherapy
    note is never bound to a session in the product; the row is here only so
    the policy is proven on the restricted side too.
    """
    ids: dict[str, str] = {}
    now = datetime.now(UTC).replace(microsecond=0)
    conn = _connect(engine, tenant_schema, user_id=_AUTHOR)
    try:
        for kind, restricted in (("progress", False), ("psychotherapy", True)):
            note_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO notes (id, patient_id, note_type, content, status, "
                    "author_user_id, restricted, finalized_at, created_at, updated_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), :t, "
                    "CAST(:c AS jsonb), 'complete', CAST(:a AS uuid), :r, :now, :now, :now)"
                ),
                {
                    "id": note_id,
                    "pid": patient,
                    "t": "psychotherapy" if restricted else "narrative",
                    "c": '{"body": "x"}',
                    "a": _AUTHOR,
                    "r": restricted,
                    "now": now,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO note_signatures (id, note_id, patient_id, version, note_type, "
                    "content, digest, signed_by, signer_name, signed_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:nid AS uuid), CAST(:pid AS uuid), 1, "
                    ":t, CAST(:c AS jsonb), :d, CAST(:a AS uuid), 'Author', :now)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "nid": note_id,
                    "pid": patient,
                    "t": "narrative",
                    "c": '{"body": "x"}',
                    "d": "0" * 64,
                    "a": _AUTHOR,
                    "now": now,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO note_addenda (id, note_id, patient_id, text, signer_name, "
                    "digest, created_by, created_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:nid AS uuid), CAST(:pid AS uuid), "
                    "'added', 'Author', :d, CAST(:a AS uuid), :now)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "nid": note_id,
                    "pid": patient,
                    "d": "1" * 64,
                    "a": _AUTHOR,
                    "now": now,
                },
            )
            session_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, "
                    "session_number, status, transcript, created_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:a AS uuid), CAST(:pid AS uuid), :now, 1, "
                    "'pending_review', CAST(:t AS jsonb), :now)"
                ),
                {
                    "id": session_id,
                    "a": _AUTHOR,
                    "pid": patient,
                    "t": '{"format": "txt", "content": "x"}',
                    "now": now,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO session_dictations (id, session_id, note_id, patient_id, "
                    "author_user_id, audio_path, content_type, status, created_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:sid AS uuid), CAST(:nid AS uuid), "
                    "CAST(:pid AS uuid), CAST(:a AS uuid), 'dictations/x', 'audio/webm', "
                    "'transcribing', :now)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "sid": session_id,
                    "nid": note_id,
                    "pid": patient,
                    "a": _AUTHOR,
                    "now": now,
                },
            )
            ids[kind] = note_id
        conn.commit()
    finally:
        conn.close()
    return ids


def _visible_notes(conn: Connection, table: str) -> set[str]:
    rows = conn.execute(text(f"SELECT note_id::text FROM {table}")).scalars().all()  # noqa: S608 — module constant
    return set(rows)


class TestRoleReallyEnforcesRls:
    def test_connecting_role_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).first()
        assert row is not None
        assert not row[0], "connecting role is a superuser; RLS would be bypassed"
        assert not row[1], "connecting role has BYPASSRLS; RLS would be bypassed"


class TestPolicyShipped:
    @pytest.mark.parametrize("table", _CHILDREN)
    def test_the_note_child_policy_is_on_and_forced(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        with engine.connect() as conn:
            policies = set(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": table},
                ).scalars()
            )
            forced = conn.execute(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = :t"
                ),
                {"s": tenant_schema, "t": table},
            ).scalar()
        assert policies == {"rls_note_child_access"}
        assert forced is True


@pytest.mark.parametrize("table", _CHILDREN)
class TestFollowsTheNote:
    def test_the_author_reads_both(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str], table: str
    ) -> None:
        conn = _connect(engine, tenant_schema, user_id=_AUTHOR)
        try:
            assert _visible_notes(conn, table) == set(notes.values())
        finally:
            conn.close()

    def test_a_co_treater_reads_the_progress_notes_and_not_the_psychotherapy_notes(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str], table: str
    ) -> None:
        conn = _connect(engine, tenant_schema, user_id=_CO_TREATER)
        try:
            visible = _visible_notes(conn, table)
            assert notes["progress"] in visible, "control: the co-treater's grant is live"
            assert notes["psychotherapy"] not in visible
        finally:
            conn.close()

    def test_a_co_treater_cannot_write_to_the_psychotherapy_note(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str], table: str
    ) -> None:
        conn = _connect(engine, tenant_schema, user_id=_CO_TREATER)
        try:
            deleted = conn.execute(
                text(f"DELETE FROM {table} WHERE note_id = CAST(:id AS uuid)"),  # noqa: S608
                {"id": notes["psychotherapy"]},
            ).rowcount
            conn.rollback()
        finally:
            conn.close()
        assert deleted == 0

    @pytest.mark.usefixtures("notes")
    def test_an_outsider_reads_nothing(
        self, engine: Engine, tenant_schema: str, table: str
    ) -> None:
        control = _connect(engine, tenant_schema, user_id=_AUTHOR)
        try:
            assert _visible_notes(control, table), "control: rows exist"
        finally:
            control.close()
        conn = _connect(engine, tenant_schema, user_id=_OUTSIDER)
        try:
            assert _visible_notes(conn, table) == set()
        finally:
            conn.close()

    @pytest.mark.usefixtures("notes")
    def test_the_patient_principal_reads_nothing(
        self, engine: Engine, tenant_schema: str, patient: str, table: str
    ) -> None:
        conn = _connect(engine, tenant_schema)
        try:
            conn.execute(
                text("SELECT set_config('app.current_patient_id', :p, false)"), {"p": patient}
            )
            own = conn.execute(
                text("SELECT id FROM patients WHERE id = CAST(:id AS uuid)"), {"id": patient}
            ).all()
            assert own, "control: the patient principal is armed"
            assert _visible_notes(conn, table) == set()
        finally:
            conn.close()
