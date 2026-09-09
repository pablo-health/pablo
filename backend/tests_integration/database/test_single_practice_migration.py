# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Moving a pre-provisioning deployment onto its own practice schema.

The operation turns FORCE ROW LEVEL SECURITY on over records that have been
living without it, and under FORCE RLS a row satisfying no policy does not
raise — it becomes invisible. So the assertions here are about rows that must
still be readable afterwards, counted per table rather than spot-checked,
because a spot check on a chart that IS there says nothing about the one that
is not.

The fixture builds the OLD shape deliberately: a ``practice`` schema holding
live rows, registered in ``platform.practices`` as the deployment's practice.
That is what an install from before the split actually looks like, and none of
this is exercised by provisioning a fresh tenant.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import (
    DEFAULT_PRACTICE_ID,
    DEFAULT_PRACTICE_OWN_SCHEMA,
    DEFAULT_PRACTICE_SCHEMA,
    PLATFORM_SCHEMA,
    enable_rls_on_schema,
)
from app.db.provisioning import create_practice_schema, ensure_schemas
from app.db.single_practice_migration import (
    PreflightError,
    Shape,
    _classify,
    is_migrated,
    migrate,
    preflight,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _counts(counts, table: str):
    for c in counts:
        if c.table == table:
            return c
    raise AssertionError(f"{table} not in pre-flight results: {[c.table for c in counts]}")


# ---------------------------------------------------------------------------
# The drift guard: does _classify still describe the real policies?
# ---------------------------------------------------------------------------


#: What ``enable_rls_on_schema`` names the policy it creates, per shape. The
#: pre-flight's predicates are only correct while these still line up; a new
#: branch there without one here fails this test rather than silently making the
#: pre-flight under-report.
_POLICY_NAME_BY_SHAPE = {
    Shape.USER_ID: "rls_user_isolation",
    Shape.AUDIT_LOGS: "rls_audit_actor_access",
    Shape.UPLOADER: "rls_user_isolation",
    Shape.PATIENT_ACCESS_BY_ID: "rls_patient_access",
    Shape.PATIENT_ACCESS_BY_PATIENT_ID: "rls_patient_access",
    Shape.PATIENT_DOCUMENTS: "rls_patient_doc_access",
    Shape.CHAT_MESSAGES: "rls_chat_message_access",
}


def test_classification_matches_the_policies_actually_created(engine: Engine) -> None:
    """Pin the pre-flight's model of RLS against the real thing.

    ``single_practice_migration`` mirrors ``enable_rls_on_schema``'s branch
    chain, and a copy can drift from its original. A drifted copy is worse than
    no copy: it reports zero invisible rows for a shape it no longer
    understands. So apply the REAL policies to a real schema, read
    ``pg_policies`` back, and assert the classifier agrees.
    """

    schema = f"practice_classify_{uuid.uuid4().hex[:8]}"
    try:
        create_practice_schema(engine, schema)
        with Session(engine) as session:
            # The policies call ``has_patient_access`` unqualified and it is
            # schema-local, so it has to be resolvable from the search_path.
            session.execute(text(f"SET search_path = {schema}, {PLATFORM_SCHEMA}, public"))
            enable_rls_on_schema(session, schema)
            session.commit()

        with engine.connect() as conn:
            actual: dict[str, set[str]] = {}
            for table, policy in conn.execute(
                text("SELECT tablename, policyname FROM pg_policies WHERE schemaname = :s"),
                {"s": schema},
            ):
                actual.setdefault(table, set()).add(policy)

            columns: dict[str, set[str]] = {}
            for table, column in conn.execute(
                text(
                    "SELECT table_name, column_name FROM information_schema.columns "
                    "WHERE table_schema = :s AND table_name != 'alembic_version'"
                ),
                {"s": schema},
            ):
                columns.setdefault(table, set()).add(column)

        assert actual, f"no policies found on {schema} — the fixture is wrong, not the code"

        for table, policies in actual.items():
            shape = _classify(table, columns[table])
            expected = _POLICY_NAME_BY_SHAPE[shape]
            assert expected in policies, (
                f"pre-flight classifies {table} as {shape.value}, which implies policy "
                f"'{expected}', but enable_rls_on_schema created {sorted(policies)}. "
                f"The pre-flight's predicate for this table is describing a policy that "
                f"no longer exists."
            )
    finally:
        with engine.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))


# ---------------------------------------------------------------------------
# The old shape, and moving out of it
# ---------------------------------------------------------------------------


@pytest.fixture
def old_shape(engine: Engine):
    """A deployment as it looked before the template and live practice split.

    ``practice`` holds the charts and the registry points at it. Restored
    afterwards so the session-scoped template database is left as found.
    """

    # Park whatever the shared database already holds under these two names so
    # this test owns them outright. Everything after the first rename runs inside
    # the try, so a setup failure still restores — a leaked ``_parked_template``
    # breaks every later test in the module with DuplicateSchema, which reads as
    # eleven broken tests rather than the one that actually failed.
    with engine.begin() as conn:
        conn.execute(text(f'ALTER SCHEMA "{DEFAULT_PRACTICE_SCHEMA}" RENAME TO "_parked_template"'))
        parked_own = bool(
            conn.execute(
                text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
                {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
            ).first()
        )
        if parked_own:
            conn.execute(
                text(f'ALTER SCHEMA "{DEFAULT_PRACTICE_OWN_SCHEMA}" RENAME TO "_parked_own"')
            )

    try:
        create_practice_schema(engine, DEFAULT_PRACTICE_SCHEMA)
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO {PLATFORM_SCHEMA}.practices "  # noqa: S608 — schema/table are validated identifiers, never user input
                    f"(id, name, schema_name, owner_email, product, status, "
                    f"provisioning_status, is_active, created_at) "
                    f"VALUES (:id, 'Default Practice', :schema, '', 'pablo', 'active', "
                    f"'ready', true, now()) "
                    f"ON CONFLICT (id) DO UPDATE SET schema_name = EXCLUDED.schema_name"
                ),
                {"id": DEFAULT_PRACTICE_ID, "schema": DEFAULT_PRACTICE_SCHEMA},
            )
        yield engine
    finally:
        with engine.begin() as conn:
            for name in (DEFAULT_PRACTICE_SCHEMA, DEFAULT_PRACTICE_OWN_SCHEMA):
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
            conn.execute(
                text(f"DELETE FROM {PLATFORM_SCHEMA}.practices WHERE id = :id"),  # noqa: S608
                {"id": DEFAULT_PRACTICE_ID},
            )
            conn.execute(
                text(f'ALTER SCHEMA "_parked_template" RENAME TO "{DEFAULT_PRACTICE_SCHEMA}"')
            )
            if parked_own:
                conn.execute(
                    text(f'ALTER SCHEMA "_parked_own" RENAME TO "{DEFAULT_PRACTICE_OWN_SCHEMA}"')
                )


@pytest.fixture(scope="module")
def superuser_engine() -> Iterator[Engine]:
    """A connection that can see past the policies, for counting what is ON DISK.

    The suite's ``pablo`` role is deliberately NOSUPERUSER NOBYPASSRLS — that is
    the whole point of the RLS invariants — so it cannot answer "did every row
    survive the move". Under FORCE RLS with no GUC armed it correctly sees zero,
    which is the right answer to a different question.

    So the physical-preservation assertions use the container's bootstrap
    superuser. Visibility THROUGH the policies is asserted separately, as each
    owner, in ``test_migration_leaves_every_row_readable_by_its_owner`` — the two
    together are what "nothing was lost and everything is still reachable" means.
    """
    su_url = _db_url.replace("pablo:pablo_dev@", "postgres:postgres_dev@", 1)
    if su_url == _db_url:
        pytest.skip(
            "cannot derive the bootstrap superuser URL from DATABASE_URL; these "
            "assertions need a role that can read past RLS"
        )
    eng = create_engine(su_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _count(engine, schema: str, table: str) -> int:
    """Count rows with the schema on the search_path.

    After the migration the policies are live, and ``has_patient_access``'s body
    names ``patient_clinicians`` unqualified — so it resolves against the
    CALLER's search_path, not the function's. Reading a migrated schema without
    setting it raises ``relation "patient_clinicians" does not exist``, which is
    the same arrangement ``set_tenant_schema`` makes for every real request.

    Pass the superuser engine when the question is "is the row there", and the
    ordinary one when it is "can this principal see it".
    """
    with engine.connect() as conn:
        conn.execute(text(f'SET search_path = "{schema}", {PLATFORM_SCHEMA}, public'))
        return conn.execute(text(f'SELECT count(*) FROM "{schema}"."{table}"')).scalar_one()  # noqa: S608


def _seed_patient(conn, schema: str, user_id: str, *, with_grant: bool = True) -> str:
    """A patient row, optionally with the grant that makes it readable.

    ``patients`` carries no ``user_id``: ownership lives entirely in
    ``patient_clinicians``. That is precisely why a missing grant is
    unrecoverable rather than merely inconvenient — there is no column left on
    the row that says whose chart it was.
    """
    patient_id = str(uuid.uuid4())
    conn.execute(
        text(
            f"INSERT INTO {schema}.patients (id, first_name, last_name, "  # noqa: S608 — schema/table are validated identifiers, never user input
            f"first_name_lower, last_name_lower, status, session_count, "
            f"created_at, updated_at) "
            f"VALUES (:p, 'Test', 'Patient', 'test', 'patient', 'active', 0, now(), now())"
        ),
        {"p": patient_id},
    )
    if with_grant:
        conn.execute(
            text(
                f"INSERT INTO {schema}.patient_clinicians "  # noqa: S608 — schema/table are validated identifiers, never user input
                f"(patient_id, user_id, role, granted_by) VALUES (:p, :u, 'primary', :u)"
            ),
            {"p": patient_id, "u": user_id},
        )
    return patient_id


def test_preflight_is_clean_for_properly_granted_data(old_shape) -> None:
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        for _ in range(3):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id)

    counts = preflight(engine, DEFAULT_PRACTICE_SCHEMA)

    patients = _counts(counts, "patients")
    assert patients.total == 3
    assert patients.orphaned == 0
    assert all(c.orphaned == 0 for c in counts), [
        (c.table, c.orphaned) for c in counts if c.orphaned
    ]


def test_preflight_counts_a_patient_with_no_grant(old_shape) -> None:
    """The failure mode the whole pre-flight exists for.

    A patients row with no live ``patient_clinicians`` grant does not error under
    the policy — it disappears.
    """
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=True)
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=False)

    patients = _counts(preflight(engine, DEFAULT_PRACTICE_SCHEMA), "patients")

    assert patients.total == 2
    assert patients.orphaned == 1
    assert patients.shape is Shape.PATIENT_ACCESS_BY_ID


def test_an_expired_grant_counts_as_no_grant(old_shape) -> None:
    """``has_patient_access`` checks ``expires_at``; so must the pre-flight.

    An expired grant is the subtle case — the row LOOKS granted, and a
    liveness-blind check would report the migration safe while the chart
    vanishes.
    """
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        patient_id = _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=True)
        conn.execute(
            text(
                f"UPDATE {DEFAULT_PRACTICE_SCHEMA}.patient_clinicians "  # noqa: S608 — schema/table are validated identifiers, never user input
                f"SET expires_at = now() - interval '1 day' WHERE patient_id = :p"
            ),
            {"p": patient_id},
        )

    assert _counts(preflight(engine, DEFAULT_PRACTICE_SCHEMA), "patients").orphaned == 1


def test_migrate_refuses_when_the_preflight_is_dirty(old_shape) -> None:
    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()), with_grant=False)

    with pytest.raises(PreflightError) as exc:
        migrate(engine)

    assert "patients" in str(exc.value)
    # And nothing moved.
    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": DEFAULT_PRACTICE_SCHEMA},
        ).first()
        row = conn.execute(
            text(f"SELECT schema_name FROM {PLATFORM_SCHEMA}.practices WHERE id = :id"),  # noqa: S608
            {"id": DEFAULT_PRACTICE_ID},
        ).one()
    assert row[0] == DEFAULT_PRACTICE_SCHEMA


def test_migration_preserves_every_row_per_table(old_shape, superuser_engine) -> None:
    """Counts before and after, per table — not a spot check.

    A spot check on a chart that survived says nothing about one that did not,
    and "some rows are still there" is exactly what a partial loss looks like.
    """
    engine = old_shape
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        for _ in range(4):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_a)
        for _ in range(2):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_b)

    def table_counts(schema: str) -> dict[str, int]:
        out: dict[str, int] = {}
        with engine.connect() as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_type = 'BASE TABLE'"
                    ),
                    {"s": schema},
                )
            ]
        for t in tables:
            out[t] = _count(superuser_engine, schema, t)
        return out

    before = table_counts(DEFAULT_PRACTICE_SCHEMA)
    migrate(engine)
    after = table_counts(DEFAULT_PRACTICE_OWN_SCHEMA)

    assert before == after, {
        t: (before.get(t), after.get(t))
        for t in set(before) | set(after)
        if before.get(t) != after.get(t)
    }
    assert before["patients"] == 6


def test_migration_leaves_every_row_readable_by_its_owner(old_shape) -> None:
    """The end state that matters: with policies on, each clinician still sees
    their own charts — through the policy, as the app would."""
    engine = old_shape
    user_a, user_b = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        for _ in range(4):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_a)
        for _ in range(2):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_b)

    migrate(engine)

    for user_id, expected in ((user_a, 4), (user_b, 2)):
        with engine.connect() as conn:
            conn.execute(text(f'SET search_path = "{DEFAULT_PRACTICE_OWN_SCHEMA}"'))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id}
            )
            visible = conn.execute(text("SELECT count(*) FROM patients")).scalar_one()
        assert visible == expected, f"{user_id} sees {visible} of their {expected} patients"


def test_rls_is_actually_on_afterwards(old_shape) -> None:
    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)

    with engine.connect() as conn:
        forced = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relname = 'patients'"
            ),
            {"s": DEFAULT_PRACTICE_OWN_SCHEMA},
        ).one()
    assert forced == (True, True), "patients is not force-RLS'd after the migration"


def test_the_template_comes_back(old_shape, superuser_engine) -> None:
    """The migration renames the template away; every future practice is cloned
    from it, so it has to be rebuilt — empty, and a template again."""
    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)

    with engine.connect() as conn:
        assert conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :s"),
            {"s": DEFAULT_PRACTICE_SCHEMA},
        ).first(), "the template schema was not rebuilt"
        assert (
            conn.execute(
                text(f"SELECT count(*) FROM {DEFAULT_PRACTICE_SCHEMA}.patients")  # noqa: S608
            ).scalar_one()
            == 0
        ), "the rebuilt template is not empty"


def test_running_it_twice_is_safe(old_shape, superuser_engine) -> None:
    engine = old_shape
    with engine.begin() as conn:
        for _ in range(3):
            _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)
    assert is_migrated(engine)

    migrate(engine)  # must not raise, must not move anything

    assert _count(superuser_engine, DEFAULT_PRACTICE_OWN_SCHEMA, "patients") == 3


def test_boot_refuses_against_an_unmigrated_deployment(old_shape) -> None:
    """Criterion 5: boot fails with an actionable message, not a half-state.

    This used to warn and carry on, which meant serving a chart schema with no
    row policies while the deployment looked healthy. A warning in a startup log
    is not a control — nobody is reading it.
    """

    engine = old_shape

    with pytest.raises(RuntimeError) as exc:
        ensure_schemas(engine)

    message = str(exc.value)
    assert "migrate_default_practice" in message, message
    assert DEFAULT_PRACTICE_SCHEMA in message
    assert DEFAULT_PRACTICE_OWN_SCHEMA in message


def test_boot_is_happy_once_migrated(old_shape) -> None:
    """And the refusal lifts by doing the thing it asked for — otherwise it is
    just a deployment that can never start."""

    engine = old_shape
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, str(uuid.uuid4()))

    migrate(engine)
    ensure_schemas(engine)  # must not raise

    assert is_migrated(engine)


def test_force_migrates_despite_orphans(old_shape, superuser_engine) -> None:
    """``--force`` is for an operator who read the report and decided. It skips
    the refusal, not the counting."""
    engine = old_shape
    user_id = str(uuid.uuid4())
    with engine.begin() as conn:
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=True)
        _seed_patient(conn, DEFAULT_PRACTICE_SCHEMA, user_id, with_grant=False)

    counts = migrate(engine, force=True)

    assert _counts(counts, "patients").orphaned == 1
    assert is_migrated(engine)
    # The orphan is still ON DISK — force means "migrate anyway", not "delete".
    assert _count(superuser_engine, DEFAULT_PRACTICE_OWN_SCHEMA, "patients") == 2
