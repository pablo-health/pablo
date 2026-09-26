# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for author-only notes.

A psychotherapy note is the clinician's own analysis of a session, kept
apart from the rest of the record. ``notes`` gained two columns for it —
``author_user_id`` and ``restricted`` — and its row policy grew a second
arm: an ordinary note still follows ``has_patient_access`` (co-treaters
share the chart), a restricted one is readable by its author alone.

Three principals, one chart:

* the **author**, who wrote both a progress note and a psychotherapy note
  on patient A;
* a **co-treater**, who holds a ``patient_clinicians`` grant on A and so
  sees the progress note — and must not see the psychotherapy note;
* the **patient** principal, who reads nothing from ``notes`` at all.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion is
preceded by a visibility control on the same connection, so no assertion can
pass because the table was empty, the schema was wrong or the GUC was never
armed. The role's RLS-bypass bits are asserted up front for the same reason.

The policy is applied by ``enable_rls_on_schema`` at provisioning and by the
per-practice reconcile after every upgrade; ``test_rls_reconcile_on_migrate``
proves a migrated practice ends up with the same policy map as a fresh one,
and names this policy so the drift detector cannot lose it quietly.

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

_NOTES = "notes"

# Wrote every note in this module, and holds a grant on the patient.
_AUTHOR = "6c2a8f13-9d4e-5b70-a1c3-2e8f5d7b4a91"
# Holds a grant on the same patient; wrote nothing.
_CO_TREATER = "9f1d3b57-2c8a-5e46-b7d0-4a6c1e9f8b23"


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

    schema = f"practice_test_notes_rls_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient(engine: Engine, tenant_schema: str) -> str:
    """One patient, with a grant for the author and one for the co-treater."""
    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _AUTHOR},
        )
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
        # The grant table is policed by ``user_id``: each clinician's own
        # grant row is written as that clinician, as the app does.
        for user_id in (_AUTHOR, _CO_TREATER):
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": user_id},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :granted_by)"
                ),
                {"pid": patient_id, "u": user_id, "granted_by": _AUTHOR},
            )
    return patient_id


@pytest.fixture(scope="module")
def notes(engine: Engine, tenant_schema: str, patient: str) -> dict[str, str]:
    """A progress note and a psychotherapy note, both by the author."""
    ids: dict[str, str] = {}
    conn = _as_clinician(engine, tenant_schema, _AUTHOR)
    try:
        ids["progress"] = _insert(conn, patient_id=patient, restricted=False)
        ids["psychotherapy"] = _insert(conn, patient_id=patient, restricted=True)
        conn.commit()
    finally:
        conn.close()
    return ids


# ---------------------------------------------------------------------------
# Connections and writes
# ---------------------------------------------------------------------------


def _as_clinician(engine: Engine, schema: str, user_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_patient_id"))
    conn.execute(
        text("SELECT set_config('app.current_user_id', :u, false)"),
        {"u": user_id},
    )
    return conn


def _as_patient(engine: Engine, schema: str, patient_id: str) -> Connection:
    conn = engine.connect()
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("RESET app.current_user_id"))
    conn.execute(
        text("SELECT set_config('app.current_patient_id', :p, false)"),
        {"p": patient_id},
    )
    return conn


def _insert(conn: Connection, *, patient_id: str, restricted: bool) -> str:
    """Write a standalone note as whoever the connection is armed as.

    The row policy's ``USING`` arm is what a write has to satisfy as well,
    so an INSERT the author could not read back would be refused here
    rather than leave an empty table for the read tests to pass against.
    """
    note_id = str(uuid.uuid4())
    author = conn.execute(text("SELECT current_setting('app.current_user_id', true)")).scalar()
    conn.execute(
        text(
            f"INSERT INTO {_NOTES} "  # noqa: S608 — module constant, no caller input
            "(id, patient_id, session_id, note_type, content, status, "
            "author_user_id, restricted, created_at, updated_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), NULL, :note_type, "
            "CAST(:content AS jsonb), 'complete', CAST(:author AS uuid), :restricted, "
            ":now, :now)"
        ),
        {
            "id": note_id,
            "pid": patient_id,
            "note_type": "psychotherapy" if restricted else "narrative",
            "content": '{"note": {"body": "x"}}',
            "author": author,
            "restricted": restricted,
            "now": datetime.now(UTC).replace(microsecond=0),
        },
    )
    return note_id


def _visible(conn: Connection) -> set[str]:
    rows = conn.execute(text(f"SELECT id::text FROM {_NOTES}")).scalars().all()  # noqa: S608
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

    def test_the_columns_are_there(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            columns = dict(
                conn.execute(
                    text(
                        "SELECT column_name, is_nullable FROM information_schema.columns "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": tenant_schema, "t": _NOTES},
                ).all()
            )
        assert columns["author_user_id"] == "YES"
        assert columns["restricted"] == "NO"

    def test_the_policy_is_the_two_arm_one(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT policyname, qual FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = :t"
                ),
                {"s": tenant_schema, "t": _NOTES},
            ).all()
        policies = dict(rows)
        assert "rls_note_access" in policies, sorted(policies)
        assert "rls_patient_access" not in policies
        assert "restricted" in policies["rls_note_access"]
        assert "author_user_id" in policies["rls_note_access"]


class TestAuthorOnly:
    def test_the_author_reads_both_notes(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str]
    ) -> None:
        conn = _as_clinician(engine, tenant_schema, _AUTHOR)
        try:
            assert _visible(conn) == set(notes.values())
        finally:
            conn.close()

    def test_a_co_treater_reads_the_progress_note_and_not_the_psychotherapy_note(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str]
    ) -> None:
        conn = _as_clinician(engine, tenant_schema, _CO_TREATER)
        try:
            visible = _visible(conn)
            assert notes["progress"] in visible, "control: the co-treater's grant is live"
            assert notes["psychotherapy"] not in visible
            by_id = conn.execute(
                text(f"SELECT id FROM {_NOTES} WHERE id = CAST(:id AS uuid)"),  # noqa: S608
                {"id": notes["psychotherapy"]},
            ).all()
            assert by_id == [], "asking for it by id is no different from listing"
        finally:
            conn.close()

    def test_a_co_treater_cannot_edit_or_delete_it_either(
        self, engine: Engine, tenant_schema: str, notes: dict[str, str]
    ) -> None:
        conn = _as_clinician(engine, tenant_schema, _CO_TREATER)
        try:
            updated = conn.execute(
                text(
                    f"UPDATE {_NOTES} SET updated_at = now() "  # noqa: S608
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": notes["psychotherapy"]},
            ).rowcount
            deleted = conn.execute(
                text(f"DELETE FROM {_NOTES} WHERE id = CAST(:id AS uuid)"),  # noqa: S608
                {"id": notes["psychotherapy"]},
            ).rowcount
            conn.rollback()
        finally:
            conn.close()
        assert updated == 0
        assert deleted == 0

    @pytest.mark.usefixtures("notes")
    def test_the_patient_principal_reads_no_notes_at_all(
        self, engine: Engine, tenant_schema: str, patient: str
    ) -> None:
        """``notes`` is the clinician's record about the patient, not one
        for them: it is not patient-readable, restricted or not."""
        conn = _as_patient(engine, tenant_schema, patient)
        try:
            own_row = conn.execute(
                text("SELECT id FROM patients WHERE id = CAST(:id AS uuid)"),
                {"id": patient},
            ).all()
            assert own_row, "control: the patient principal is armed and sees its own chart row"
            assert _visible(conn) == set()
        finally:
            conn.close()
