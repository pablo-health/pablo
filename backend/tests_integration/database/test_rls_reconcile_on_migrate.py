# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The tenant fan-out re-applies row policies to practices that already exist.

A revision creates tables. It does not create policies — those come from
``enable_rls_on_schema``, which used to run only when a schema was
provisioned. So a practice created yesterday was correct (the template
carries the policies) while a practice created last month got the new table
with no row-level security at all, and a table whose registration changed
kept the policy set it was born with. Both states are invisible from inside
a test suite that provisions every schema fresh, which is why this one
starts from a fresh schema and then makes it look like an old one.

Two shapes, both observed on the dev deployment:

* **A table the practice never had policies for.** Strip
  ``patient_message_threads`` back to no RLS and no policies — what a
  revision leaves behind when it creates a table in a schema that already
  exists — and assert the fan-out restores the full set, that a patient may
  write their own thread, and that they may not write someone else's.
* **A registration that changed shape.** ``patient_intake_submissions``
  gained a patient write arm after it shipped; a practice provisioned
  before that keeps the read-only set, and the patient's own submission is
  refused with ``new row violates row-level security policy``. That is the
  500 the dev intake run hit, reproduced here before the fan-out and gone
  after it.

Then the general form of both: a schema stripped of every policy and healed
by the fan-out must end up with the same policies, on the same tables, as a
freshly provisioned one. That is what fails if a future revision adds a
patient-scoped table and nothing teaches the reconcile about it.

Runs as the ``pablo`` role, which the integration conftest creates
``NOSUPERUSER NOBYPASSRLS`` — without that the write assertions would pass
against no policy at all. Run: ``make test-integration``.
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
from app.db.migrate_tenants import fan_out
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

_THREADS = "patient_message_threads"
_SUBMISSIONS = "patient_intake_submissions"
# The clinician who created both patients, and so holds a grant on each.
_TREATING_CLINICIAN = "3a7e1c82-45bd-5f19-8c62-0e4d7b3a9f15"


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
def reference(engine: Engine) -> Iterator[str]:
    """A practice provisioned today — the state every practice should hold."""
    schema = _new_schema(engine, "rls_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture
def aged(engine: Engine) -> Iterator[tuple[str, str, str]]:
    """A practice each test ages by hand, plus two patients of its own.

    Function-scoped: every test damages its schema, and a shared one would
    make the order the tests run in part of what they assert.
    """
    schema = _new_schema(engine, "rls_aged")
    patient_a, patient_b = _seed_two_patients(engine, schema)
    yield schema, patient_a, patient_b
    _drop_schema(engine, schema)


def _seed_two_patients(engine: Engine, schema: str) -> tuple[str, str]:
    """Create two patients clinician-side, as the app would."""
    patient_a = str(uuid.uuid4())
    patient_b = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _TREATING_CLINICIAN},
        )
        for pid, first, last in ((patient_a, "Ada", "Lovelace"), (patient_b, "Grace", "Hopper")):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, "
                    "first_name_lower, last_name_lower, status, session_count, "
                    "created_at, updated_at) "
                    "VALUES (CAST(:pid AS uuid), :first, :last, lower(:first), "
                    "lower(:last), 'active', 0, now(), now())"
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


def _start_thread(conn: Connection, patient_id: str) -> None:
    """Insert one message thread owned by *patient_id* — a patient's own write."""
    conn.execute(
        text(
            f"INSERT INTO {_THREADS} "  # noqa: S608 — module constant, not caller input
            "(id, patient_id, subject, status, created_at, last_message_at) "
            "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), :subject, 'open', now(), now())"
        ),
        {"id": str(uuid.uuid4()), "pid": patient_id, "subject": "A question"},
    )


def _submit_intake(conn: Connection, patient_id: str) -> None:
    """Insert one intake submission owned by *patient_id*."""
    conn.execute(
        text(
            f"INSERT INTO {_SUBMISSIONS} "  # noqa: S608 — module constant
            "(id, patient_id, submitted_at, payload, created_by, created_at) "
            "VALUES (:id, CAST(:pid AS uuid), :now, CAST(:payload AS jsonb), :by, :now)"
        ),
        {
            "id": f"intake-{uuid.uuid4().hex}",
            "pid": patient_id,
            "now": datetime.now(UTC).replace(microsecond=0),
            "payload": '{"reason_for_visit": "seed"}',
            "by": patient_id,
        },
    )


def _age_table(engine: Engine, schema: str, table: str) -> None:
    """Leave *table* as a revision does: present, with no RLS and no policies."""
    with engine.begin() as conn:
        for (policy,) in conn.execute(
            text("SELECT policyname FROM pg_policies WHERE schemaname = :s AND tablename = :t"),
            {"s": schema, "t": table},
        ).all():
            conn.execute(text(f'DROP POLICY "{policy}" ON "{schema}"."{table}"'))
        conn.execute(text(f'ALTER TABLE "{schema}"."{table}" NO FORCE ROW LEVEL SECURITY'))
        conn.execute(text(f'ALTER TABLE "{schema}"."{table}" DISABLE ROW LEVEL SECURITY'))


def _drop_policy(engine: Engine, schema: str, table: str, policy: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(f'DROP POLICY IF EXISTS "{policy}" ON "{schema}"."{table}"'))


def _policy_names(engine: Engine, schema: str, table: str) -> set[str]:
    with engine.connect() as conn:
        return {
            row[0]
            for row in conn.execute(
                text("SELECT policyname FROM pg_policies WHERE schemaname = :s AND tablename = :t"),
                {"s": schema, "t": table},
            ).all()
        }


def _rls_flags(engine: Engine, schema: str, table: str) -> tuple[bool, bool]:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = :t"
                ),
                {"s": schema, "t": table},
            )
            .mappings()
            .one()
        )
    return row["relrowsecurity"], row["relforcerowsecurity"]


def _policy_map(engine: Engine, schema: str) -> dict[tuple[str, str], tuple[str, str, str, str]]:
    """Every policy in *schema*, keyed by (table, policy), schema name removed.

    ``pg_get_expr`` qualifies ``has_patient_access`` with whichever schema
    owns the copy, so the text differs between two schemas holding identical
    policies. Stripping the schema name is what makes the two comparable —
    and leaves every other difference visible.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename, policyname, permissive, cmd, "
                "COALESCE(qual, ''), COALESCE(with_check, '') "
                "FROM pg_policies WHERE schemaname = :s"
            ),
            {"s": schema},
        ).all()

    def _strip(expr: str) -> str:
        return expr.replace(f'"{schema}".', "").replace(f"{schema}.", "")

    return {
        (table, policy): (permissive, cmd, _strip(qual), _strip(with_check))
        for table, policy, permissive, cmd, qual, with_check in rows
    }


def _rls_flag_map(engine: Engine, schema: str) -> dict[str, tuple[bool, bool]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relkind = 'r'"
            ),
            {"s": schema},
        ).all()
    return {name: (rowsec, forced) for name, rowsec, forced in rows}


class TestRoleReallyEnforcesRls:
    """If this fails, every write assertion below is meaningless."""

    def test_connecting_role_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).first()
        assert row is not None
        assert not row[0], "connecting role is a superuser; RLS would be bypassed"
        assert not row[1], "connecting role has BYPASSRLS; RLS would be bypassed"


def test_a_table_with_no_policies_is_protected_by_the_fan_out(
    engine: Engine, aged: tuple[str, str, str], reference: str
) -> None:
    """The dev state for the tables recent revisions added to old practices."""
    schema, patient_a, patient_b = aged
    _age_table(engine, schema, _THREADS)
    assert _rls_flags(engine, schema, _THREADS) == (False, False), (
        "the aged state this test depends on was not reached"
    )

    results = fan_out(engine, [schema])

    assert all(r.ok for r in results), [(r.schema, r.status, r.detail) for r in results]
    assert _rls_flags(engine, schema, _THREADS) == (True, True)
    assert _policy_names(engine, schema, _THREADS) == _policy_names(engine, reference, _THREADS)

    own = _as_patient(engine, schema, patient_a)
    try:
        _start_thread(own, patient_a)
        own.commit()
    finally:
        own.close()

    foreign = _as_patient(engine, schema, patient_a)
    try:
        with pytest.raises(ProgrammingError, match="row-level security"):
            _start_thread(foreign, patient_b)
    finally:
        foreign.rollback()
        foreign.close()


def test_a_registration_that_gained_a_write_arm_reaches_an_old_practice(
    engine: Engine, aged: tuple[str, str, str]
) -> None:
    """The exact dev failure: a patient refused their own intake submission.

    ``patient_intake_submissions`` was registered read-only when it shipped
    and writable later. A practice provisioned in between holds the earlier
    set, and nothing about it looks wrong until a patient submits a form.
    """
    schema, patient_a, _ = aged
    for policy in ("rls_patient_self_insert", "rls_patient_self_write"):
        _drop_policy(engine, schema, _SUBMISSIONS, policy)

    refused = _as_patient(engine, schema, patient_a)
    try:
        with pytest.raises(ProgrammingError, match="row-level security"):
            _submit_intake(refused, patient_a)
    finally:
        refused.rollback()
        refused.close()

    results = fan_out(engine, [schema])

    assert all(r.ok for r in results), [(r.schema, r.status, r.detail) for r in results]
    assert "rls_patient_self_insert" in _policy_names(engine, schema, _SUBMISSIONS)

    accepted = _as_patient(engine, schema, patient_a)
    try:
        _submit_intake(accepted, patient_a)
        accepted.commit()
    finally:
        accepted.close()


def test_a_migrated_schema_ends_up_matching_a_freshly_provisioned_one(
    engine: Engine, aged: tuple[str, str, str], reference: str
) -> None:
    """The general form: no table may be left behind by the reconcile.

    Comparing whole policy maps rather than one table's is what makes this a
    drift detector — a future revision that adds a patient-scoped table the
    reconcile cannot policy shows up here, on the table nobody named.
    """
    schema, _, _ = aged
    with engine.connect() as conn:
        tables = [
            row[0]
            for row in conn.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = :s"),
                {"s": schema},
            ).all()
        ]
    for table in tables:
        _age_table(engine, schema, table)
    assert not _policy_map(engine, schema), "the aged state this test depends on was not reached"

    results = fan_out(engine, [schema])

    assert all(r.ok for r in results), [(r.schema, r.status, r.detail) for r in results]
    assert _policy_map(engine, schema) == _policy_map(engine, reference)
    assert _rls_flag_map(engine, schema) == _rls_flag_map(engine, reference)
