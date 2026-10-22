# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Provisioning applies row-level security from the template; the reconcile verifies.

``tenant_template.sql`` carries the row-level security ``enable_rls_on_schema``
gives a practice schema, captured from that function's own output. So a
practice built from the template is isolated as soon as the template is
applied, and the reconcile that provisioning still runs afterwards finds
nothing to change. These hold that arrangement to account:

* **The verify changes nothing on a template-built schema.** If it did, the
  template and the function disagree, and provisioning would quietly be
  paying for the rebuild again.
* **The template's policies are the function's.** A schema built from the
  template's schema part alone, then given its row-level security by a full
  rebuild, ends with exactly the flags and policies a template-built one
  has — compared catalog to catalog, so formatting cannot hide a difference.
* **It is still the repair pass.** A dropped policy, RLS switched off, a
  policy altered by hand: each is put back, and only that table is touched.
* **The template schema stays unforced.** ``practice`` is built from the
  schema part only.

Runs as the ``pablo`` role (``NOSUPERUSER NOBYPASSRLS``, from the integration
conftest). Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import event, text

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

_POLICY_STATE = """
    SELECT c.relname, p.polname, p.polcmd::text, p.polpermissive,
           pg_catalog.array_to_string(ARRAY(
               SELECT CASE WHEN r = 0 THEN 'public' ELSE pg_catalog.pg_get_userbyid(r) END
               FROM pg_catalog.unnest(p.polroles) AS r ORDER BY 1), ','),
           pg_catalog.pg_get_expr(p.polqual, p.polrelid),
           pg_catalog.pg_get_expr(p.polwithcheck, p.polrelid),
           pg_catalog.obj_description(p.oid, 'pg_policy')
    FROM pg_catalog.pg_policy p
    JOIN pg_catalog.pg_class c ON c.oid = p.polrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema
"""
_FLAG_STATE = """
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema AND c.relkind IN ('r', 'p')
"""


def _new_schema(label: str) -> str:
    return f"practice_test_{label}_{uuid.uuid4().hex[:8]}"


def _drop_schema(engine: Engine, schema: str) -> None:
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _provision(engine: Engine, label: str) -> str:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    schema = _new_schema(label)
    create_practice_schema(engine, schema)
    return schema


def _reconcile(engine: Engine, schema: str) -> Any:
    from app.db.provisioning import reconcile_practice_rls  # noqa: PLC0415

    return reconcile_practice_rls(engine, schema)


def _state(engine: Engine, schema: str) -> tuple[set[tuple[Any, ...]], set[tuple[Any, ...]]]:
    """Every table's RLS flags and every policy, with the schema name taken out."""
    from app.db.rls_state import schema_neutral  # noqa: PLC0415

    def neutral(value: Any) -> Any:
        return schema_neutral(value, schema) if isinstance(value, str) else value

    with engine.connect() as conn:
        # Deparse with every name qualified, whatever the pool left behind.
        conn.execute(text("SET search_path = pg_catalog"))
        flags = {tuple(row) for row in conn.execute(text(_FLAG_STATE), {"schema": schema})}
        policies = {
            tuple(neutral(v) for v in row)
            for row in conn.execute(text(_POLICY_STATE), {"schema": schema})
        }
        conn.rollback()
    return flags, policies


@pytest.fixture(scope="module")
def provisioned(engine: Engine) -> Iterator[str]:
    """A practice provisioned the ordinary way: template, then the verify."""
    schema = _provision(engine, "rls_tpl")
    yield schema
    _drop_schema(engine, schema)


@pytest.fixture(scope="module")
def rebuilt(engine: Engine) -> Iterator[tuple[str, Any]]:
    """A practice built from the schema part alone and given RLS by a full rebuild."""
    from app.db.provisioning import (  # noqa: PLC0415
        _apply_tenant_template,
        _stamp_alembic_at_head,
    )

    schema = _new_schema("rls_rebuilt")
    _apply_tenant_template(engine, schema)
    _stamp_alembic_at_head(engine, schema)
    counts = _reconcile(engine, schema)
    yield schema, counts
    _drop_schema(engine, schema)


@pytest.fixture
def damaged(engine: Engine) -> Iterator[str]:
    """A fresh practice for a test to damage; function-scoped so no test sees another's."""
    schema = _provision(engine, "rls_dmg")
    yield schema
    _drop_schema(engine, schema)


def test_the_verify_alters_nothing_on_a_template_built_schema(
    engine: Engine, provisioned: str
) -> None:
    counts = _reconcile(engine, provisioned)
    assert counts.applied > 0
    assert counts.altered == 0, (
        "the template and enable_rls_on_schema disagree on "
        f"{counts.altered} table(s); regenerate tenant_template.sql"
    )


def test_the_template_carries_what_a_full_rebuild_produces(
    engine: Engine, provisioned: str, rebuilt: tuple[str, Any]
) -> None:
    rebuilt_schema, counts = rebuilt
    # Built without the template's row-level security, so the pass had to
    # set every table it applies to.
    assert counts.altered == counts.applied > 0

    template_flags, template_policies = _state(engine, provisioned)
    rebuilt_flags, rebuilt_policies = _state(engine, rebuilt_schema)
    assert template_flags == rebuilt_flags
    assert template_policies == rebuilt_policies
    assert any(forced for _, _, forced in template_flags)


def test_the_template_schema_itself_stays_unforced(engine: Engine) -> None:
    flags, _ = _state(engine, "practice")
    assert flags, "the template schema has no tables"
    assert not any(enabled or forced for _, enabled, forced in flags)


def test_a_dropped_policy_is_put_back(engine: Engine, damaged: str) -> None:
    before = _state(engine, damaged)
    with engine.connect() as conn:
        conn.execute(text(f"DROP POLICY rls_user_isolation ON {damaged}.availability_rules"))
        conn.commit()

    counts = _reconcile(engine, damaged)

    assert counts.altered == 1
    assert _state(engine, damaged) == before
    assert _reconcile(engine, damaged).altered == 0


def test_rls_switched_off_is_switched_back_on(engine: Engine, damaged: str) -> None:
    before = _state(engine, damaged)
    with engine.connect() as conn:
        conn.execute(text(f"ALTER TABLE {damaged}.notes NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {damaged}.patients DISABLE ROW LEVEL SECURITY"))
        conn.commit()

    counts = _reconcile(engine, damaged)

    assert counts.altered == 2
    assert _state(engine, damaged) == before


def test_a_policy_altered_by_hand_is_restored(engine: Engine, damaged: str) -> None:
    before = _state(engine, damaged)
    with engine.connect() as conn:
        # Keeps the policy, its name and its fingerprint comment; only the
        # stored predicate changes, which is what the fingerprint also covers.
        conn.execute(
            text(f"ALTER POLICY rls_user_isolation ON {damaged}.availability_rules USING (true)")
        )
        conn.commit()

    assert _reconcile(engine, damaged).altered == 1
    assert _state(engine, damaged) == before


def test_a_not_row_scoped_table_forced_on_is_switched_off(engine: Engine, damaged: str) -> None:
    before = _state(engine, damaged)
    with engine.connect() as conn:
        conn.execute(text(f"ALTER TABLE {damaged}.payers ENABLE ROW LEVEL SECURITY"))
        conn.commit()

    assert _reconcile(engine, damaged).altered == 1
    assert _state(engine, damaged) == before


def test_the_verify_runs_a_handful_of_statements(engine: Engine, provisioned: str) -> None:
    """The point of carrying RLS in the template: verifying costs reads, not a rebuild."""
    executed: list[str] = []

    def _count(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        executed.append(statement)

    event.listen(engine, "before_cursor_execute", _count)
    try:
        counts = _reconcile(engine, provisioned)
    finally:
        event.remove(engine, "before_cursor_execute", _count)

    ddl = [s for s in executed if s.lstrip().upper().startswith(("ALTER TABLE", "DROP", "CREATE"))]
    assert counts.altered == 0
    # The practice directory's pass re-asserts its own three policies.
    assert len([s for s in ddl if "rls_practice_directory_read" not in s]) == 0, ddl
    assert len(executed) < 40, len(executed)
