# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The audit_logs policy exists in three hand-maintained copies.

The same predicate is written out in
``alembic/versions/b2d6f83a4c19_audit_logs_actor_split_policy.py`` (for
schemas that existed when it ran), in ``enable_rls_on_schema``
(``app/db/__init__.py``, for schemas provisioned afterwards), and in
``tenant_template.sql`` (regenerated, but by hand and in a separate
commit). Nothing makes the three agree.

Drift between them is close to undetectable by ordinary means: a
migrated tenant and a freshly-provisioned one would both have a policy,
both accept the writes their tests exercise, and differ only in some
arm nobody happened to test on both. Given what the arms decide — which
patient can write a row in whose name — "close enough on one of the two
paths" is not a state worth being in.

So this compares what Postgres actually ended up with, catalog to
catalog, rather than comparing the three sources textually: formatting,
casts and parenthesisation all differ between a hand-written
``CREATE POLICY`` and a ``pg_dump``-derived one, while ``pg_policies``
renders both through the same deparser.
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

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
    """Migrate to head so the alembic chain and the tenant template exist."""
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def provisioned_schema(engine: Engine) -> Iterator[str]:
    """A tenant built the other way: template DDL + ``enable_rls_on_schema``."""
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_parity_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def migrated_schema(engine: Engine) -> Iterator[str]:
    """A tenant schema carrying the MIGRATION's policy, under forced RLS.

    ``practice`` is the obvious migrated exemplar and the wrong one:
    ``enable_rls_on_schema`` returns early for the default schema, so
    ``practice`` has the policy row and RLS switched off, and comparing
    against it would assert the parity guarantee on the one schema where
    the predicate cannot fire.

    So: provision a tenant the ordinary way (which forces RLS), drop the
    policy ``enable_rls_on_schema`` just wrote, and re-create it from the
    migration module's own SQL constants. What remains is a schema whose
    policy came from the migration path, enforcing, and comparable to one
    whose policy came from the provisioning path.
    """
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Loaded by path: ``alembic/versions`` is a script directory, not an
    # importable package, and a bare ``alembic.versions`` resolves to the
    # installed alembic library instead.
    migration_path = (
        Path(__file__).resolve().parents[2]
        / "alembic"
        / "versions"
        / "b2d6f83a4c19_audit_logs_actor_split_policy.py"
    )
    spec = importlib.util.spec_from_file_location("_audit_split_policy", migration_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_migrated_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    with engine.begin() as conn:
        conn.execute(text(f"DROP POLICY IF EXISTS rls_audit_actor_access ON {schema}.audit_logs"))
        conn.execute(
            text(
                f"CREATE POLICY rls_audit_actor_access ON {schema}.audit_logs "
                f"USING ({module._SELECT_USING}) WITH CHECK ({module._INSERT_CHECK})"
            )
        )
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


def _policies(engine: Engine, schema: str, table: str = "audit_logs") -> list[dict]:
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT policyname, permissive, cmd, roles::text AS roles, "
                    "qual, with_check FROM pg_policies "
                    "WHERE schemaname = :s AND tablename = :t "
                    "ORDER BY policyname"
                ),
                {"s": schema, "t": table},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


class TestBothProvisioningPathsAgree:
    def test_the_migrated_and_provisioned_policies_are_identical(
        self, engine: Engine, migrated_schema: str, provisioned_schema: str
    ) -> None:
        """Same name, same command, same USING, same WITH CHECK.

        If this fails, one of the three copies of the predicate was
        edited and the others were not — and the failure message names
        which arm moved.

        Both sides are schemas with RLS forced, deliberately. An earlier
        version of this test used ``practice`` as the migrated side,
        where RLS is off — which compared the two predicates honestly
        enough but read as though it also proved the migrated path was
        protected, on the one schema where it is not.
        """
        migrated = _policies(engine, migrated_schema)
        provisioned = _policies(engine, provisioned_schema)

        assert migrated == provisioned

    def test_audit_logs_carries_exactly_one_policy(
        self, engine: Engine, migrated_schema: str, provisioned_schema: str
    ) -> None:
        """The single-``ALL``-policy decision is load-bearing and easy to
        undo by accident.

        Splitting it into SELECT-only and INSERT-only policies would
        leave UPDATE and DELETE with no policy at all, and a command
        with no policy does not error — it matches no rows. A tampering
        attempt would stop being a loud trigger error and become a
        quiet no-op. A leftover ``rls_user_isolation`` from an
        incomplete drop would be just as bad in the other direction:
        policies are OR'd, so the old permissive one would widen the
        new one back out.
        """
        for schema in (migrated_schema, provisioned_schema):
            policies = _policies(engine, schema)
            assert [p["policyname"] for p in policies] == ["rls_audit_actor_access"], schema
            assert policies[0]["cmd"] == "ALL", schema
            assert policies[0]["permissive"] == "PERMISSIVE", schema

    def test_the_policy_states_its_with_check_rather_than_inheriting_it(
        self, engine: Engine, migrated_schema: str, provisioned_schema: str
    ) -> None:
        """A ``WITH CHECK`` left off an ``ALL`` policy silently defaults to
        the ``USING`` expression — which on this table would mean the
        patient arm vanishing and every patient-actor INSERT being
        refused again, with no policy error to say so. The two clauses
        are deliberately different here; asserting that they are pins
        the distinction.
        """
        for schema in (migrated_schema, provisioned_schema):
            policy = _policies(engine, schema)[0]
            assert policy["with_check"] is not None, schema
            assert policy["qual"] != policy["with_check"], schema
            assert "app.current_patient_id" in policy["with_check"], schema
            assert "app.current_patient_id" not in (policy["qual"] or ""), schema


def _rls_flags(engine: Engine, schema: str) -> dict:
    with engine.connect() as conn:
        return dict(
            conn.execute(
                text(
                    "SELECT c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :s AND c.relname = 'audit_logs'"
                ),
                {"s": schema},
            )
            .mappings()
            .one()
        )


class TestWhereThePolicyIsActuallyInForce:
    """Having a policy and being subject to one are different things.

    The app connects as the table owner, and an owner is exempt from its
    own policies unless RLS is *forced*. So the policy comparison above
    proves the two paths wrote the same predicate; it does not prove
    either one enforces it.
    """

    def test_a_per_tenant_schema_has_rls_enabled_and_forced(
        self, engine: Engine, provisioned_schema: str
    ) -> None:
        flags = _rls_flags(engine, provisioned_schema)
        assert flags["relrowsecurity"] is True
        assert flags["relforcerowsecurity"] is True

    def test_the_default_practice_schema_is_deliberately_not_under_rls(
        self, engine: Engine
    ) -> None:
        """And the policy the migration puts there is therefore inert.

        ``create_practice_schema`` skips RLS for the default template on
        purpose: a single-tenant OSS deployment runs against ``practice``
        without the middleware that arms ``app.current_user_id``, and RLS
        with no GUC armed fails closed — zero rows, on every table, for
        the whole install.

        The migration nonetheless issues ``CREATE POLICY`` against
        ``current_schema()``, so ``practice`` ends up carrying the
        predicate without being subject to it. That is not a bug, but it
        is a trap: a future reader confirming "the policy is there" on a
        single-tenant install learns nothing about whether it applies.
        Pinned here so the surprise is documented rather than
        rediscovered — and so that a change which *does* start forcing
        RLS on ``practice`` has to come past this test and decide what
        happens to those deployments.
        """
        flags = _rls_flags(engine, "practice")
        assert flags["relrowsecurity"] is False
        assert flags["relforcerowsecurity"] is False
        assert [p["policyname"] for p in _policies(engine, "practice")] == [
            "rls_audit_actor_access"
        ]
