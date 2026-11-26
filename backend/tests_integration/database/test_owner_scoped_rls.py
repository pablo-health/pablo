# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A tenant table registered owner-scoped is forced, policied and drift-healed.

``register_overlay_owner_scoped(table, owner_column)`` is how a deployment
says "each row of this table belongs to the user named in this column", for
a table whose owner column is not ``user_id``. The policy planner
(``infer_rls_on_schema``) then enables and forces row-level security on it
with one ``rls_owner_isolation`` policy. The planner runs when a tenant
template is generated, and live practices are made to match the template, so
these tests run the planner over a practice built the way the regen builds
its scratch schema (``planner_practice``).

Each test adds a throwaway table to such a practice, the way
a deployment's own migration would, and registers it for the test's
duration only. The table carries an ``id`` and a ``varchar`` owner column,
the common shape for a deployment's per-user table.

Runs as the ``pablo`` role, which the integration conftest creates
``NOSUPERUSER NOBYPASSRLS`` and which owns the tables; FORCE is what applies
the policy to an owner. Without that every isolation assertion below would
pass against no policy at all. Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from typing import TYPE_CHECKING

import pytest
from app import db as app_db
from app.db import OwnerScope, infer_rls_on_schema, register_overlay_owner_scoped
from app.db.rls_state import read_live_rls
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session as OrmSession

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

_TABLE = "deployment_owned_notes"
_USER_A = "user-a-3f9c1e"
_USER_B = "user-b-7d2a4b"


def _new_schema(engine: Engine) -> str:
    from tests_integration.database.planner_practice import (  # noqa: PLC0415
        build_planner_practice,
    )

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_owner_{uuid.uuid4().hex[:8]}"
    build_planner_practice(engine, schema)
    return schema


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, OwnerScope]:
    """An empty owner-scoped registry, put back after the test.

    The registry is process-wide; a registration left behind would make every
    later provisioning in the run look for a table it does not have.
    """
    fresh: dict[str, OwnerScope] = {}
    monkeypatch.setattr(app_db, "_OVERLAY_OWNER_SCOPED", fresh)
    return fresh


@pytest.fixture
def schema(engine: Engine) -> Iterator[str]:
    """A provisioned practice holding the throwaway table, no rows yet."""
    name = _new_schema(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE {name}.{_TABLE} ("
                "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
                "owner_user_id varchar(64), "
                "body text NOT NULL)"
            )
        )
    yield name
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
        conn.commit()


def _reconcile(engine: Engine, schema: str) -> app_db.RlsReconcileCounts:
    """The planner, strict, as the template regen runs it."""
    with OrmSession(engine) as session:
        session.execute(text(f"SET search_path = {schema}, platform, public"))
        return infer_rls_on_schema(session, schema)


def _insert(engine: Engine, schema: str, as_user: str, owner: str | None, body: str) -> None:
    """Insert one row through row security, armed as ``as_user``."""
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": as_user})
        conn.execute(
            text(f"INSERT INTO {_TABLE} (owner_user_id, body) VALUES (:o, :b)"),  # noqa: S608
            {"o": owner, "b": body},
        )


def _hand_over(engine: Engine, schema: str, *, as_user: str, to: str) -> None:
    """Set the owner of every row ``as_user`` can see to ``to``."""
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL search_path = {schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": as_user})
        conn.execute(text(f"UPDATE {_TABLE} SET owner_user_id = :to"), {"to": to})  # noqa: S608


def _visible(engine: Engine, schema: str, as_user: str | None) -> set[str]:
    """The bodies ``as_user`` can read; ``None`` reads with no principal armed."""
    with engine.begin() as conn:
        conn.execute(text(f"SET LOCAL search_path = {schema}, platform, public"))
        if as_user is not None:
            conn.execute(text("SELECT set_config('app.current_user_id', :u, true)"), {"u": as_user})
        rows = conn.execute(text(f"SELECT body FROM {_TABLE}")).scalars().all()  # noqa: S608
    return set(rows)


def _flags(engine: Engine, schema: str) -> tuple[bool, bool]:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = :s AND c.relname = :t"
            ),
            {"s": schema, "t": _TABLE},
        ).one()
    return bool(row[0]), bool(row[1])


def _policies(engine: Engine, schema: str) -> list[tuple[str, list[str]]]:
    """``(policy, roles)`` for every policy on the throwaway table."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT policyname, roles FROM pg_policies "
                "WHERE schemaname = :s AND tablename = :t ORDER BY policyname"
            ),
            {"s": schema, "t": _TABLE},
        ).all()
    return [(name, list(roles)) for name, roles in rows]


class TestRoleReallyEnforcesRls:
    """If this fails, every isolation assertion below is meaningless."""

    def test_connecting_role_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).first()
        assert row is not None
        assert not row[0], "connecting role is a superuser; RLS would be bypassed"
        assert not row[1], "connecting role has BYPASSRLS; RLS would be bypassed"


@pytest.mark.usefixtures("registry")
class TestOwnerScoped:
    def test_forced_with_one_policy_for_the_app_role(self, engine: Engine, schema: str) -> None:
        """(a) Forced, and exactly one policy, which applies to every role."""
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        counts = _reconcile(engine, schema)

        assert counts.altered == 1, "only the registered table should have needed work"
        assert _flags(engine, schema) == (True, True)
        assert _policies(engine, schema) == [("rls_owner_isolation", ["public"])]

    def test_a_table_with_none_of_the_scoping_columns_is_still_planned(
        self, engine: Engine, schema: str
    ) -> None:
        """The registration is what brings a table in, not an ``id`` column.

        Without it this table would never enter the reconcile, and the
        registration would be a silent no-op: RLS off, every row readable.
        """
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {schema}.{_TABLE} DROP COLUMN id"))
            conn.execute(text(f"ALTER TABLE {schema}.{_TABLE} ADD PRIMARY KEY (body)"))
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)

        assert _flags(engine, schema) == (True, True)
        assert _policies(engine, schema) == [("rls_owner_isolation", ["public"])]
        _insert(engine, schema, _USER_B, _USER_B, "b's row")
        assert _visible(engine, schema, _USER_A) == set()

    def test_a_sees_own_row_and_not_bs(self, engine: Engine, schema: str) -> None:
        """(b) Each user reads their own rows and nobody else's."""
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)
        _insert(engine, schema, _USER_A, _USER_A, "a's row")
        _insert(engine, schema, _USER_B, _USER_B, "b's row")

        assert _visible(engine, schema, _USER_A) == {"a's row"}
        assert _visible(engine, schema, _USER_B) == {"b's row"}

    def test_a_cannot_write_a_row_owned_by_b(self, engine: Engine, schema: str) -> None:
        """(c) Neither by inserting it nor by handing an own row over."""
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)

        with pytest.raises(ProgrammingError, match="row-level security"):
            _insert(engine, schema, _USER_A, _USER_B, "planted on b")

        _insert(engine, schema, _USER_A, _USER_A, "a's row")
        with pytest.raises(ProgrammingError, match="row-level security"):
            _hand_over(engine, schema, as_user=_USER_A, to=_USER_B)
        assert _visible(engine, schema, _USER_B) == set()

    def test_a_null_owner_is_nobodys_by_default(self, engine: Engine, schema: str) -> None:
        """Without ``shared_when_null`` a NULL owner matches no principal."""
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)

        with pytest.raises(ProgrammingError, match="row-level security"):
            _insert(engine, schema, _USER_A, None, "shared?")

    def test_an_unarmed_session_sees_nothing(self, engine: Engine, schema: str) -> None:
        """(d) No principal, no rows: the policy fails closed."""
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)
        _insert(engine, schema, _USER_A, _USER_A, "a's row")

        assert _visible(engine, schema, _USER_A) == {"a's row"}, "control"
        assert _visible(engine, schema, None) == set()

    def test_an_empty_setting_does_not_match_an_empty_owner(
        self, engine: Engine, schema: str
    ) -> None:
        """A pooled connection reads the setting back as '' once it was armed.

        ``set_config(..., true)`` leaves ``''`` behind, not NULL, after its
        transaction ends. The owner arm must still require a principal, or an
        unarmed session matches every row whose owner column is ''.
        """
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)

        with engine.begin() as conn:
            # Planted past the policy, as the table owner with FORCE lifted.
            conn.execute(text(f"ALTER TABLE {schema}.{_TABLE} NO FORCE ROW LEVEL SECURITY"))
            conn.execute(
                text(f"INSERT INTO {schema}.{_TABLE} (owner_user_id, body) VALUES ('', 'empty')")  # noqa: S608
            )
            conn.execute(text(f"ALTER TABLE {schema}.{_TABLE} FORCE ROW LEVEL SECURITY"))

        with engine.connect() as conn:
            with conn.begin():
                conn.execute(
                    text("SELECT set_config('app.current_user_id', :u, true)"), {"u": _USER_A}
                )
            with conn.begin():
                setting = conn.execute(
                    text("SELECT current_setting('app.current_user_id', true)")
                ).scalar()
                rows = conn.execute(text(f"SELECT body FROM {schema}.{_TABLE}")).scalars().all()  # noqa: S608
                with (
                    pytest.raises(ProgrammingError, match="row-level security"),
                    conn.begin_nested(),
                ):
                    conn.execute(
                        text(
                            f"INSERT INTO {schema}.{_TABLE} (owner_user_id, body) "  # noqa: S608
                            "VALUES ('', 'planted unarmed')"
                        )
                    )

        assert setting == "", "control: the setting must read back empty, not NULL"
        assert rows == []

    def test_shared_when_null(self, engine: Engine, schema: str) -> None:
        """(e) A NULL-owner row is everyone's; an owned row is still its owner's."""
        register_overlay_owner_scoped(_TABLE, "owner_user_id", shared_when_null=True)
        _reconcile(engine, schema)
        _insert(engine, schema, _USER_A, None, "shared")
        _insert(engine, schema, _USER_B, _USER_B, "b's row")

        assert _visible(engine, schema, _USER_A) == {"shared"}
        assert _visible(engine, schema, _USER_B) == {"shared", "b's row"}
        assert _visible(engine, schema, None) == set(), "the shared arm still needs a principal"

    def test_a_missing_owner_column_raises(self, engine: Engine, schema: str) -> None:
        """(f) A registration on a column the table lacks is refused, not shipped."""
        register_overlay_owner_scoped(_TABLE, "clinician_id")

        with pytest.raises(RuntimeError, match="registered owner-scoped on 'clinician_id'"):
            _reconcile(engine, schema)
        assert _flags(engine, schema) == (False, False), "nothing may run before the raise"

    def test_registered_both_ways_raises(
        self, engine: Engine, schema: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_db, "_OVERLAY_NOT_ROW_SCOPED", {_TABLE})
        register_overlay_owner_scoped(_TABLE, "owner_user_id")

        with pytest.raises(RuntimeError, match="both owner-scoped and not-row-scoped"):
            _reconcile(engine, schema)

    def test_moving_to_not_row_scoped_drops_the_policy(
        self,
        engine: Engine,
        schema: str,
        registry: dict[str, OwnerScope],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """(g) A withdrawn registration leaves no owner policy in the next template.

        Withdrawn into not-row-scoped, which is the only way a table carrying
        an ``id`` can stop being row-scoped: unregistered outright, it is a
        table the engine no longer knows, and the planner leaves those as it
        found them (``infer_rls_on_schema``'s ``strict``). Registrations act
        when a template is generated, so this is the planner's pass.
        """
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)
        assert _policies(engine, schema) == [("rls_owner_isolation", ["public"])], "control"

        registry.clear()
        monkeypatch.setattr(app_db, "_OVERLAY_NOT_ROW_SCOPED", {_TABLE})
        _reconcile(engine, schema)

        # RLS off is the not-row-scoped state; DISABLE leaves the FORCE bit as
        # it was, which means nothing while RLS is off (core's heal is the same).
        assert _flags(engine, schema)[0] is False
        assert _policies(engine, schema) == []

    def test_the_reconcile_heals_a_dropped_or_altered_policy(
        self, engine: Engine, schema: str
    ) -> None:
        """The planner restores a damaged policy, and verifies it after.

        A live practice is healed by the run-time pass against the templates
        (covered with the template tests); this is the planner's own verify,
        which the regen relies on when it builds its scratch practice.
        """
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        _reconcile(engine, schema)
        _insert(engine, schema, _USER_A, _USER_A, "a's row")
        _insert(engine, schema, _USER_B, _USER_B, "b's row")

        with engine.begin() as conn:
            conn.execute(
                text(f"ALTER POLICY rls_owner_isolation ON {schema}.{_TABLE} USING (true)")
            )
        assert _visible(engine, schema, _USER_A) == {"a's row", "b's row"}, "damage reached"

        assert _reconcile(engine, schema).altered == 1
        assert _visible(engine, schema, _USER_A) == {"a's row"}

        with engine.begin() as conn:
            conn.execute(text(f"DROP POLICY rls_owner_isolation ON {schema}.{_TABLE}"))
        assert _visible(engine, schema, _USER_A) == set(), "forced with no policy: deny-all"

        _reconcile(engine, schema)
        assert _policies(engine, schema) == [("rls_owner_isolation", ["public"])]
        assert _visible(engine, schema, _USER_A) == {"a's row"}
        assert _reconcile(engine, schema).altered == 0, "healed state must verify, not rebuild"


def test_a_registration_changes_no_other_tables_policy(
    engine: Engine, registry: dict[str, OwnerScope], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering a table changes no other table's policy in the planner's output.

    A practice the planner built is planned again with the registration in
    place. Every table whose plan the registration touched would be rebuilt;
    they are read off that pass itself, through ``record_fingerprints``. The
    registered table is absent from the schema, which the registry allows (it
    is process-wide), so nothing may be rebuilt at all.
    """
    rebuilt: set[str] = set()
    real_record = app_db.record_fingerprints

    def spy(session: OrmSession, schema_name: str, plans: dict[str, app_db.TablePlan]) -> None:
        rebuilt.update(plans)
        real_record(session, schema_name, plans)

    name = _new_schema(engine)
    try:
        monkeypatch.setattr(app_db, "record_fingerprints", spy)
        register_overlay_owner_scoped(_TABLE, "owner_user_id")
        with OrmSession(engine) as session:
            before = read_live_rls(session, name)
        counts = _reconcile(engine, name)
        with OrmSession(engine) as session:
            after = read_live_rls(session, name)
    finally:
        with engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
            conn.commit()

    assert rebuilt == set(), "the registration rebuilt these tables' policies"
    assert counts.altered == 0, "a planner-built practice must verify unchanged"
    assert counts.applied > 0
    assert before == after
    assert registry == {_TABLE: OwnerScope("owner_user_id", False)}
