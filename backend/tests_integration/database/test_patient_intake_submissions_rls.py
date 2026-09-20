# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres isolation proof for ``patient_intake_submissions``.

The table is registered patient-readable AND patient-writable, which is a
wider grant than most per-patient chart tables get: a patient inserts their
own row and reads it back. This proves the policies that grant carries
actually hold, against a schema built by the real ``create_practice_schema``
(so the policies under test are the ones that ship, created by
``enable_rls_on_schema``) and connected as the ``pablo`` role, which the
integration conftest creates ``NOSUPERUSER NOBYPASSRLS`` exactly as
production has it.

**Non-vacuity is enforced, not hoped for.** Every invisibility assertion is
preceded by a visibility control on the same connection, so no assertion
can pass because the table was empty, the schema was wrong or the GUC was
never armed. The role's RLS-bypass bits are asserted up front for the same
reason.

The second half proves the two producers of this table agree. A freshly
provisioned tenant gets it from ``tenant_template.sql``; an existing tenant
gets it from the alembic revision. Those are different code paths, and the
classic failure is that one ships and the other does not — which surfaces
far from the cause, as ``relation ... does not exist`` in whatever runs
next. So the columns, indexes and policy names are compared between a
template-built schema and a chain-built one, and the chain is also run over
a schema that already holds the table to prove the create is the no-op its
``IF NOT EXISTS`` claims to be.

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

_TABLE = "patient_intake_submissions"
_INDEX = "ix_patient_intake_submissions_patient_id"
# The clinician who created both patients, and so holds a
# ``patient_clinicians`` grant on each.
_TREATING_CLINICIAN = "6c2f9a41-8b3d-5e7a-9f04-1d8c3b6e2a95"
# A clinician in the same practice with no grant on either patient.
_STRANGER_CLINICIAN = "b1d0f47e-2c95-5a83-9e61-4f07a2c8d316"

# The DDL a deployment may already carry from a prior extension that created
# this table before the engine owned it. Byte-identical to the revision's,
# which is the whole point: the revision must be a no-op against it.
_PRIOR_DDL = """
CREATE TABLE IF NOT EXISTS patient_intake_submissions (
    id            VARCHAR(128) PRIMARY KEY,
    patient_id    UUID         NOT NULL,
    submitted_at  TIMESTAMPTZ  NOT NULL,
    payload       JSONB        NOT NULL,
    created_by    VARCHAR(128) NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL
);
"""
_PRIOR_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS ix_patient_intake_submissions_patient_id "
    "ON patient_intake_submissions (patient_id);"
)

# The revision this one follows. Rolling a schema back to it and forward
# again replays exactly the revision under test.
_PARENT_REVISION = "b3d8f1a06c57"


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
    schema = _new_schema(engine, "intake")
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
def submissions(
    engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
) -> tuple[str, str]:
    """One intake submission per patient, each written BY that patient.

    Deliberately not seeded clinician-side. The insert runs on a connection
    with only ``app.current_patient_id`` armed, so it exercises the write
    arm the registration grants — if that arm were missing, FORCE ROW LEVEL
    SECURITY would refuse the INSERT and this fixture would error rather
    than quietly leaving an empty table for the read tests to pass against.
    """
    ids: list[str] = []
    for patient_id in two_patients:
        submission_id = f"intake-{patient_id}"
        conn = _as_patient(engine, tenant_schema, patient_id)
        try:
            _submit(conn, patient_id, submission_id)
            conn.commit()
        finally:
            conn.close()
        ids.append(submission_id)
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


def _submit(conn: Connection, patient_id: str, submission_id: str) -> None:
    """Insert one intake submission owned by *patient_id*."""
    conn.execute(
        text(
            f"INSERT INTO {_TABLE} "  # noqa: S608 — fixed table name, no caller input
            "(id, patient_id, submitted_at, payload, created_by, created_at) "
            "VALUES (:id, CAST(:pid AS uuid), :now, CAST(:payload AS jsonb), :by, :now)"
        ),
        {
            "id": submission_id,
            "pid": patient_id,
            "now": datetime.now(UTC).replace(microsecond=0),
            "payload": '{"reason_for_visit": "seed"}',
            "by": patient_id,
        },
    )


def _visible_ids(conn: Connection) -> set[str]:
    # ``_TABLE`` is a module constant, not caller input.
    rows = conn.execute(text(f"SELECT id FROM {_TABLE}")).scalars().all()  # noqa: S608
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
                    {"s": tenant_schema, "t": _TABLE},
                )
                .mappings()
                .first()
            )
        assert row is not None, (
            f"{_TABLE} missing from a freshly-provisioned tenant — the migration "
            "or the tenant_template.sql regen did not ship"
        )
        assert row["relrowsecurity"] is True
        assert row["relforcerowsecurity"] is True
        assert row["policy_count"] >= 1, "RLS forced with no policy is a silent deny-all"

    def test_both_patient_arms_are_present(self, engine: Engine, tenant_schema: str) -> None:
        """Registered readable AND writable, so read, update and insert arms exist."""
        with engine.connect() as conn:
            by_name = dict(
                conn.execute(
                    text(
                        "SELECT policyname, cmd FROM pg_policies "
                        "WHERE schemaname = :s AND tablename = :t"
                    ),
                    {"s": tenant_schema, "t": _TABLE},
                ).all()
            )
        assert by_name.get("rls_patient_self_read") == "SELECT", by_name
        assert by_name.get("rls_patient_self_insert") == "INSERT", by_name
        assert "rls_patient_access" in by_name, by_name


class TestPatientPrincipalIsolation:
    def test_each_patient_can_submit_and_read_back_their_own(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        submissions: tuple[str, str],
    ) -> None:
        """The write control. Without it the refusals below prove nothing.

        The rows were inserted by the fixture, each under its own patient's
        armed GUC and nothing else — so reaching this assertion at all
        already means the insert arm admitted a patient writing their own
        row. This reads one back to prove the read arm agrees.
        """
        patient_a, patient_b = two_patients
        submission_a, submission_b = submissions
        for patient_id, submission_id in ((patient_a, submission_a), (patient_b, submission_b)):
            conn = _as_patient(engine, tenant_schema, patient_id)
            try:
                assert _visible_ids(conn) == {submission_id}
            finally:
                conn.close()

    def test_a_cannot_submit_a_row_owned_by_b(
        self, engine: Engine, tenant_schema: str, two_patients: tuple[str, str]
    ) -> None:
        """WITH CHECK on the insert arm: A cannot file a form as B."""
        patient_a, patient_b = two_patients
        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            with pytest.raises(ProgrammingError) as exc:
                _submit(conn, patient_b, f"intake-forged-{patient_b}")
            assert "row-level security" in str(exc.value).lower()
            conn.rollback()
        finally:
            conn.close()

    def test_a_cannot_see_bs_submission(
        self,
        engine: Engine,
        tenant_schema: str,
        two_patients: tuple[str, str],
        submissions: tuple[str, str],
    ) -> None:
        patient_a, patient_b = two_patients
        submission_a, submission_b = submissions

        conn = _as_patient(engine, tenant_schema, patient_a)
        try:
            visible = _visible_ids(conn)
            # Control: A sees their own, so the table is not simply empty.
            assert submission_a in visible
            assert submission_b not in visible

            # The IDOR move: armed as A, name B's row outright.
            named = conn.execute(
                text(
                    f"SELECT id FROM {_TABLE} "  # noqa: S608 — fixed table name
                    "WHERE patient_id = CAST(:p AS uuid)"
                ),
                {"p": patient_b},
            ).scalars()
            assert list(named) == []
        finally:
            conn.close()

    @pytest.mark.usefixtures("submissions")
    def test_no_principal_armed_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Fail-closed. The rows exist — the fixture put them there."""
        conn = _unarmed(engine, tenant_schema)
        try:
            assert _visible_ids(conn) == set()
        finally:
            conn.close()


class TestClinicianAccess:
    def test_treating_clinician_sees_the_patients_submissions(
        self, engine: Engine, tenant_schema: str, submissions: tuple[str, str]
    ) -> None:
        """``has_patient_access`` reaches the rows a grant covers."""
        conn = _as_clinician(engine, tenant_schema, _TREATING_CLINICIAN)
        try:
            assert _visible_ids(conn) == set(submissions)
        finally:
            conn.close()

    @pytest.mark.usefixtures("submissions")
    def test_stranger_clinician_sees_nothing(self, engine: Engine, tenant_schema: str) -> None:
        """Same practice, no grant on either patient. The control is above."""
        conn = _as_clinician(engine, tenant_schema, _STRANGER_CLINICIAN)
        try:
            assert _visible_ids(conn) == set()
        finally:
            conn.close()


def _shape(engine: Engine, schema: str) -> dict[str, list[str]]:
    """Columns, indexes and policy names for the table in *schema*."""
    with engine.connect() as conn:
        columns = list(
            conn.execute(
                text(
                    "SELECT column_name || ' ' || data_type || ' ' || is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name = :t "
                    "ORDER BY column_name"
                ),
                {"s": schema, "t": _TABLE},
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
                {"s": schema, "t": _TABLE},
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
                {"s": schema, "t": _TABLE},
            )
            .scalars()
            .all()
        )
    return {"columns": columns, "indexes": indexes, "policies": policies}


def _rebuild_through_the_chain(engine: Engine, schema: str, *, prior_ddl: bool) -> None:
    """Drop the table, roll the schema back one revision, migrate forward again.

    This is how an existing tenant gets the table: not from the template but
    from the revision, replayed here over a schema that does not have it
    (``prior_ddl=False``) or over one that already does because something
    created it before the engine owned it (``prior_ddl=True``).
    """
    from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {schema}, platform, public"))
        conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_TABLE} CASCADE"))
        if prior_ddl:
            conn.execute(text(_PRIOR_DDL))
            conn.execute(text(_PRIOR_INDEX_DDL))
        conn.execute(
            text(f"UPDATE {schema}.alembic_version SET version_num = :r"),  # noqa: S608
            {"r": _PARENT_REVISION},
        )

    result = upgrade_tenant_schema(engine, schema)
    assert result.status.value == "success", result.detail


def _reapply_policies(engine: Engine, schema: str) -> None:
    """Re-run the provisioning RLS step, as ``create_practice_schema`` does.

    The chain creates the table; the policies come from
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
    schema = _new_schema(engine, "intake_fresh")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant that got the table from the revision instead."""
    schema = _new_schema(engine, "intake_migrated")
    _rebuild_through_the_chain(engine, schema, prior_ddl=False)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def adopted_schema(engine: Engine) -> Iterator[str]:
    """A tenant that already held the table before the revision ran."""
    schema = _new_schema(engine, "intake_adopted")
    _rebuild_through_the_chain(engine, schema, prior_ddl=True)
    _reapply_policies(engine, schema)
    yield schema
    _drop_schema(engine, schema)


class TestFreshAndMigratedAgree:
    """The two producers of this table must land the same shape.

    A freshly provisioned tenant gets it from ``tenant_template.sql``; an
    existing one gets it from the revision. If those drift, the symptom
    appears wherever a fresh tenant is next provisioned, not here — so
    they are compared directly.
    """

    def test_template_and_chain_produce_the_same_table(
        self, engine: Engine, fresh_schema: str, migrated_schema: str
    ) -> None:
        fresh = _shape(engine, fresh_schema)
        migrated = _shape(engine, migrated_schema)
        assert fresh["columns"], f"{_TABLE} absent from the template-built schema"
        assert migrated["columns"], f"{_TABLE} absent from the chain-built schema"
        assert fresh == migrated

    def test_the_index_is_present_in_both(
        self, engine: Engine, fresh_schema: str, migrated_schema: str
    ) -> None:
        assert _INDEX in _shape(engine, fresh_schema)["indexes"]
        assert _INDEX in _shape(engine, migrated_schema)["indexes"]

    def test_revision_is_a_no_op_over_a_table_that_already_exists(
        self, engine: Engine, fresh_schema: str, adopted_schema: str
    ) -> None:
        """Adoption by identity: the create runs, changes nothing, raises nothing.

        ``_rebuild_through_the_chain`` already asserts the upgrade
        succeeded, which is half the claim; this is the other half — the
        table it left behind is the same one a fresh tenant gets.
        """
        assert _shape(engine, adopted_schema) == _shape(engine, fresh_schema)

    def test_rows_already_in_the_table_survive_adoption(self, engine: Engine) -> None:
        """A deployment's existing submissions are not dropped and recreated."""
        schema = _new_schema(engine, "intake_rows")
        try:
            patient_id = str(uuid.uuid4())
            with engine.begin() as conn:
                conn.execute(text(f"SET search_path = {schema}, platform, public"))
                conn.execute(text(f"DROP TABLE IF EXISTS {schema}.{_TABLE} CASCADE"))
                conn.execute(text(_PRIOR_DDL))
                conn.execute(text(_PRIOR_INDEX_DDL))
                _submit(conn, patient_id, "intake-preexisting")
                conn.execute(
                    text(f"UPDATE {schema}.alembic_version SET version_num = :r"),  # noqa: S608
                    {"r": _PARENT_REVISION},
                )

            from app.db.migrate_tenants import upgrade_tenant_schema  # noqa: PLC0415

            result = upgrade_tenant_schema(engine, schema)
            assert result.status.value == "success", result.detail

            # Read as the row's own patient rather than on a bare
            # connection. The chain now ends with a revision that re-runs
            # ``enable_rls_on_schema``, so the table carries its policies by
            # the time this reads it, and FORCE ROW LEVEL SECURITY means
            # even a superuser connection is subject to them. An unarmed
            # read would come back empty and look exactly like the data
            # loss this test exists to rule out.
            conn = _as_patient(engine, schema, patient_id)
            try:
                surviving = (
                    conn.execute(
                        text(f'SELECT id FROM "{schema}".{_TABLE}')  # noqa: S608
                    )
                    .scalars()
                    .all()
                )
            finally:
                conn.close()
            assert list(surviving) == ["intake-preexisting"]
        finally:
            _drop_schema(engine, schema)
