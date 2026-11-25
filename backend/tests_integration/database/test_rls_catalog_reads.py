# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The row-security reconcile reads one schema's catalog entries, not the whole catalog.

``enable_rls_on_schema`` reads three things per schema: every column (to pick
each table's policy shape), every table's RLS flags, and every policy. Each
was written as "rows of the catalog whose schema is X", and Postgres has no
index that answers that: ``pg_class`` is indexed by OID and by
name-within-schema, not by schema alone, and ``information_schema.columns``
filters only after joining the whole catalog. So every read cost what the
whole database holds, the migrate fan-out made one set per schema, and a
pass with nothing to apply grew with the square of the schema count.

``rls_state`` now starts each read from the schema's own relations, found
through ``pg_depend``. Two things to hold:

* **Nothing changed but the cost.** On a provisioned practice, plus the
  shapes a hand-made table can take (a view, a partitioned table, a domain
  over ``uuid``, a ``varchar`` key, an array, an enum, a dropped column, a
  table moved in from another schema) and a sibling schema holding the same
  names, the new reads return exactly what the old ones did.
* **The cost is the schema's, not the database's.** The work one schema's
  reads do, counted in buffers touched, is measured with 5 schemas in a
  database and again with 50: it must stay within a small factor. Buffers
  rather than seconds because they count the work itself and do not move
  with a busy CI runner. Put back, each old read fails it on its own: at 50
  schemas they touched two to seven times what they touched at 5.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from app.db.rls_state import LivePolicy, LiveRls, read_live_rls, read_schema_columns
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from tests_integration.database import scratch_db

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

# The reads as they were written before, kept as the oracle the new ones
# must agree with.
_REFERENCE_COLUMNS = (
    "SELECT table_name, column_name, data_type FROM information_schema.columns "
    "WHERE table_schema = :schema AND table_name != 'alembic_version'"
)
_REFERENCE_FLAGS = (
    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
    "FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')"
)
_REFERENCE_POLICIES = (
    "SELECT c.relname, p.polname, p.polcmd::text, p.polpermissive, "
    "pg_catalog.array_to_string(ARRAY("
    "  SELECT CASE WHEN r = 0 THEN 'public' ELSE pg_catalog.pg_get_userbyid(r) END "
    "  FROM pg_catalog.unnest(p.polroles) AS r ORDER BY 1"
    "), ','), "
    "pg_catalog.pg_get_expr(p.polqual, p.polrelid), "
    "pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid), "
    "pg_catalog.obj_description(p.oid, 'pg_policy') "
    "FROM pg_catalog.pg_policy p "
    "JOIN pg_catalog.pg_class c ON c.oid = p.polrelid "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = :schema"
)


def _reference_live_rls(session: Session, schema: str) -> LiveRls:
    # Same search_path pin as ``read_live_rls``, so ``pg_get_expr`` deparses alike.
    session.execute(text("SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)"))
    flags = {
        row[0]: (bool(row[1]), bool(row[2]))
        for row in session.execute(text(_REFERENCE_FLAGS), {"schema": schema})
    }
    policies: dict[str, dict[str, LivePolicy]] = {}
    for table, name, command, permissive, roles, using, with_check, comment in session.execute(
        text(_REFERENCE_POLICIES), {"schema": schema}
    ):
        policies.setdefault(table, {})[name] = LivePolicy(
            command, bool(permissive), roles, using, with_check, comment
        )
    return LiveRls(flags, policies)


def _drop_schemas(engine: Engine, *schemas: str) -> None:
    with engine.connect() as conn:
        for schema in schemas:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def practice(engine: Engine) -> Iterator[str]:
    """A provisioned practice, with hand-made objects of every awkward shape added.

    Also a sibling schema holding tables of the same names, which must not
    leak into the practice's reads.
    """
    from tests_integration.database.planner_practice import (  # noqa: PLC0415
        build_planner_practice,
    )

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    suffix = uuid.uuid4().hex[:8]
    schema = f"practice_test_catalog_{suffix}"
    sibling = f"catalog_sibling_{suffix}"
    build_planner_practice(engine, schema)
    statements = [
        f"CREATE SCHEMA {sibling}",
        f"CREATE TABLE {sibling}.zz_text (id int, patient_id varchar(40), only_in_sibling text)",
        f"CREATE TABLE {sibling}.zz_moved (id uuid, user_id text)",
        f"ALTER TABLE {sibling}.zz_moved SET SCHEMA {schema}",
        f"CREATE DOMAIN {schema}.zz_uuid AS uuid",
        f"CREATE TYPE {schema}.zz_mood AS ENUM ('calm', 'not_calm')",
        f"CREATE TABLE {schema}.zz_domain (id uuid, patient_id {schema}.zz_uuid)",
        f"CREATE TABLE {schema}.zz_text (id int, patient_id varchar(40), tags int[], "
        f"ids {schema}.zz_uuid[], mood {schema}.zz_mood, gone text)",
        f"ALTER TABLE {schema}.zz_text DROP COLUMN gone",
        f"CREATE TABLE {schema}.zz_parted (id int, user_id text) PARTITION BY LIST (id)",
        f"CREATE TABLE {schema}.zz_parted_1 PARTITION OF {schema}.zz_parted FOR VALUES IN (1)",
        f"ALTER TABLE {schema}.zz_parted ENABLE ROW LEVEL SECURITY",
        f"CREATE POLICY zz_own ON {schema}.zz_parted USING (user_id = current_user)",
        f"COMMENT ON POLICY zz_own ON {schema}.zz_parted IS 'hand-made'",
        f"CREATE VIEW {schema}.zz_view AS SELECT id, user_id FROM {schema}.zz_moved",  # noqa: S608
        f"CREATE SEQUENCE {schema}.zz_seq",
    ]
    with engine.connect() as conn:
        for statement in statements:
            conn.execute(text(statement))
        conn.commit()
    yield schema
    _drop_schemas(engine, schema, sibling)


def test_the_reads_return_what_the_whole_catalog_reads_returned(
    engine: Engine, practice: str
) -> None:
    with Session(engine) as session:
        columns = read_schema_columns(session, practice)
        reference_columns = [
            (row[0], row[1], row[2])
            for row in session.execute(text(_REFERENCE_COLUMNS), {"schema": practice})
        ]
        live = read_live_rls(session, practice)
        reference_live = _reference_live_rls(session, practice)

    assert sorted(columns) == sorted(reference_columns)
    assert live == reference_live

    # The comparison only means something if the awkward shapes are in it.
    types = {(table, column): data_type for table, column, data_type in columns}
    assert types[("zz_domain", "patient_id")] == "uuid"
    assert types[("zz_text", "patient_id")] == "character varying"
    assert types[("zz_text", "tags")] == "ARRAY"
    assert types[("zz_text", "mood")] == "USER-DEFINED"
    assert ("zz_text", "gone") not in types
    assert ("zz_text", "only_in_sibling") not in types
    assert {"zz_moved", "zz_view", "zz_parted", "zz_parted_1"} <= {t for t, _ in types}
    assert ("patients", "id") in types
    assert live.flags["zz_parted"] == (True, False)
    assert live.policies["zz_parted"]["zz_own"].comment == "hand-made"
    assert live.policies["patients"]


def test_the_reconcile_reads_through_the_scoped_readers(
    engine: Engine, practice: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A future edit that inlines a whole-catalog read again would go unseen above.

    The planner, as the template regen runs it over a schema.
    """
    from app import db  # noqa: PLC0415

    def reconcile_tenant_rls(engine: Engine, schema: str) -> Any:
        with Session(engine) as session:
            session.execute(text(f"SET search_path = {schema}, {db.PLATFORM_SCHEMA}, public"))
            counts = db.infer_rls_on_schema(session, schema, strict=False)
            session.commit()
            return counts

    calls: list[tuple[str, str]] = []

    def _spy(name: str, real: Any) -> Any:
        def wrapper(session: Session, schema_name: str) -> Any:
            calls.append((name, schema_name))
            return real(session, schema_name)

        return wrapper

    monkeypatch.setattr(db, "read_schema_columns", _spy("columns", read_schema_columns))
    monkeypatch.setattr(db, "read_live_rls", _spy("live", read_live_rls))

    counts = reconcile_tenant_rls(engine, practice)

    assert ("columns", practice) in calls
    assert ("live", practice) in calls
    # Same verdicts as before: a freshly provisioned practice verifies whole,
    # and the hand-made tables are passed over.
    assert counts.altered == 0
    assert counts.skipped == 6


# --- the cost does not follow the database -----------------------------------

_TABLES_PER_SCHEMA = 30


def _synthetic_schema_ddl(schema: str) -> str:
    """A schema shaped like a practice for the reads' purposes: RLS'd tables with policies."""
    ddl = [f"CREATE SCHEMA {schema}"]
    for i in range(_TABLES_PER_SCHEMA):
        table = f"{schema}.t{i}"
        ddl += [
            f"CREATE TABLE {table} (id uuid PRIMARY KEY, user_id varchar, patient_id uuid, "
            "body text, n int, at timestamptz DEFAULT now(), doc jsonb)",
            f"CREATE INDEX ON {table} (user_id)",
            f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
            f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
            f"CREATE POLICY own_read ON {table} FOR SELECT USING (user_id = current_user)",
            f"CREATE POLICY own_write ON {table} FOR INSERT WITH CHECK (user_id = current_user)",
        ]
    return "; ".join(ddl)


def _buffers_for_one_schema(scratch: Engine, schema: str) -> list[int]:
    """Buffers touched by each catalog read the two readers make for ``schema``, in order.

    The statements are captured as the readers run them, then replayed
    under ``EXPLAIN (ANALYZE, BUFFERS)``, so what is measured is the SQL
    the reconcile runs rather than a copy of it. Planning is not counted.
    """
    captured: list[tuple[str, Any]] = []

    def _capture(_conn: Any, _cursor: Any, statement: str, params: Any, *_: Any) -> None:
        if "set_config" not in statement and "current_setting" not in statement:
            captured.append((statement, params))

    with scratch.connect() as conn:
        # Twice: the first call warms the relcache and the catalog pages, so
        # the counted run is not measuring a cold start.
        for _ in range(2):
            captured.clear()
            event.listen(conn, "before_cursor_execute", _capture)
            with Session(bind=conn) as session:
                read_schema_columns(session, schema)
                read_live_rls(session, schema)
            event.remove(conn, "before_cursor_execute", _capture)
        assert len(captured) == 3
        buffers: list[int] = []
        cursor = conn.connection.dbapi_connection.cursor()  # type: ignore[union-attr]
        for statement, params in captured:
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, params)
            plan = cursor.fetchone()[0]
            plan = plan if isinstance(plan, list) else json.loads(plan)
            top = plan[0]["Plan"]
            buffers.append(top["Shared Hit Blocks"] + top["Shared Read Blocks"])
        cursor.close()
        conn.rollback()
    return buffers


@pytest.fixture
def scratch() -> Iterator[Engine]:
    """An empty database of its own, so "5 schemas" means five."""
    name = scratch_db.scratch_name("pablo_rls_catalog")
    admin = create_engine(_DB_URL, isolation_level="AUTOCOMMIT")
    try:
        scratch_db.create(admin, name)
        engine = create_engine(scratch_db.swap_database(_DB_URL, name))
        yield engine
        engine.dispose()
    finally:
        scratch_db.drop(admin, name)
        admin.dispose()


def _add_schemas(scratch: Engine, start: int, stop: int) -> None:
    with scratch.connect() as conn:
        for i in range(start, stop):
            conn.execute(text(_synthetic_schema_ddl(f"tenant_{i:03d}")))
        conn.commit()
    # Statistics as autovacuum would keep them, so the planner sees the
    # catalog's real size. The scratch database's owner may analyze it.
    with scratch.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("ANALYZE"))


def test_one_schemas_reads_cost_the_same_with_5_or_50_schemas(scratch: Engine) -> None:
    _add_schemas(scratch, 0, 5)
    with_5 = _buffers_for_one_schema(scratch, "tenant_002")
    _add_schemas(scratch, 5, 50)
    with_50 = _buffers_for_one_schema(scratch, "tenant_002")

    # Each read on its own, so one regressing cannot hide behind the others.
    # The slack allows for an index growing a level (the policy read gains
    # about a fifth); a whole-catalog read grows with the other 45 schemas'
    # rows and lands at two to seven times its first count.
    for read_index, (before, after) in enumerate(zip(with_5, with_50, strict=True)):
        assert after <= 1.5 * before + 20, (read_index, with_5, with_50)
    # And the reads did read the schema: its tables, columns and policies.
    with Session(scratch) as session:
        assert len(read_live_rls(session, "tenant_002").policies) == _TABLES_PER_SCHEMA
        assert len(read_schema_columns(session, "tenant_002")) == _TABLES_PER_SCHEMA * 7
