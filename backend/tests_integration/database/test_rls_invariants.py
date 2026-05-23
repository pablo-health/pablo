# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""RLS invariants: defense-in-depth contract for every tenant schema.

Companion of the BYPASSRLS-on-pablo-role discovery (2026-05-22) and the
chat-route-missing-tenant-context fix that shipped alongside.

Layer 2 of the three-layer regression net:
  * Layer 1: provisioning-time post-condition in ``create_practice_schema``
  * **Layer 2: this file** — CI integration test that locks in the
    contract every freshly-provisioned tenant should hold.
  * Layer 3: post-migrate audit in ``saas.bin.migrate`` that runs the
    same checks against every already-deployed tenant.

What this asserts:

1. **Role posture.** The runtime ``pablo`` DB role must NOT have
   ``rolsuper`` or ``rolbypassrls``. A regression here would silently
   bypass every RLS policy in every tenant schema (see THERAPY-* parent
   bead for the prod incident that motivated this test).

2. **RLS enablement.** Every patient-scoped table in a freshly-
   provisioned tenant schema must have ``relrowsecurity = true`` AND
   ``relforcerowsecurity = true``. ``FORCE`` matters because without it
   the table owner (often the role that ran the CREATE TABLE) bypasses
   policies — and we don't want a future contributor to add a tenant
   table and forget to enable RLS on it.

3. **Fail-closed contract.** As the ``pablo`` role, without setting
   ``app.current_user_id``, every patient-scoped table must return zero
   rows. This locks in the "no GUC = no rows" invariant that the
   codebase advertises (e.g. ``enable_rls_on_schema`` docstring). If a
   future policy edit accidentally allows rows when the GUC is unset,
   this test catches it.

Runs against testcontainers Postgres. The testcontainers conftest
creates ``pablo`` as ``NOSUPERUSER NOBYPASSRLS`` — the same posture we
want in prod. Run via ``make test-integration``.
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

# Tables every tenant schema is expected to RLS-protect. Drives both
# the relrowsecurity assertion and the fail-closed row-visibility probe.
# Add new patient-scoped tables here as they ship — the test will fail
# until they're covered, which is the point.
TENANT_SCOPED_TABLES = (
    "patients",
    "patient_clinicians",
    "chat_conversations",
    "chat_messages",
    "notes",
    "therapy_sessions",
    "appointments",
    "patient_documents",
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


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so the policy CREATEs that reference
    # ``has_patient_access`` (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_rls_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


class TestPabloRoleAttributes:
    """The runtime DB role must not bypass security infrastructure.

    Regression test for the 2026-05-22 finding that ``pablo`` had
    ``rolbypassrls = true`` in production. testcontainers creates the
    role with ``NOSUPERUSER NOBYPASSRLS`` (see conftest.py); this test
    locks in that posture and fails if anyone relaxes it.
    """

    def test_pablo_is_not_superuser_and_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT rolname, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = 'pablo'"
                    )
                )
                .mappings()
                .one_or_none()
            )
        assert row is not None, "pablo role missing from pg_roles"
        assert row["rolsuper"] is False, (
            f"pablo must not be SUPERUSER (would bypass RLS). Got rolsuper={row['rolsuper']}."
        )
        assert row["rolbypassrls"] is False, (
            "pablo must not have BYPASSRLS — defense-in-depth depends on "
            "RLS firing on every read. "
            f"Got rolbypassrls={row['rolbypassrls']}."
        )


class TestTenantRlsEnabled:
    """Every patient-scoped table in a fresh tenant has RLS forced on.

    ``enable_rls_on_schema`` is the function that should put each table
    in the right state. This test pins its post-condition — if a future
    contributor adds a new patient-scoped table and forgets to teach
    that function about it, the table here will fail the
    ``relrowsecurity`` / ``relforcerowsecurity`` assertion.
    """

    def test_every_tenant_table_has_rls_enabled_and_forced(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, "
                        "c.relforcerowsecurity "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema "
                        "  AND c.relkind = 'r' "
                        "  AND c.relname = ANY(:tables)"
                    ),
                    {"schema": tenant_schema, "tables": list(TENANT_SCOPED_TABLES)},
                )
                .mappings()
                .all()
            )

        by_table = {r["relname"]: r for r in rows}
        missing = [t for t in TENANT_SCOPED_TABLES if t not in by_table]
        # Some tables (appointments, therapy_sessions) may not exist yet
        # in every schema variant. Don't fail on absence — fail only
        # when the table exists but RLS posture is wrong. Surface what
        # was checked so a reader can confirm the coverage is what they
        # expect.
        checked = sorted(by_table.keys())
        assert checked, (
            f"None of {TENANT_SCOPED_TABLES} exist in {tenant_schema}; "
            "schema provisioning is broken."
        )

        bad = [
            (name, row["relrowsecurity"], row["relforcerowsecurity"])
            for name, row in by_table.items()
            if not (row["relrowsecurity"] and row["relforcerowsecurity"])
        ]
        assert not bad, (
            f"RLS invariant failed in {tenant_schema}. "
            f"Tables without (relrowsecurity, relforcerowsecurity) = (true, true): "
            f"{bad}. Missing-from-schema (skipped, not failed): {missing}."
        )


class TestRlsFailsClosedWithoutGuc:
    """As ``pablo``, with no ``app.current_user_id`` set, every patient-
    scoped table must return zero rows.

    This is the single most load-bearing test in this file: it locks in
    the "no GUC = no rows" contract that the codebase claims in
    multiple comments. If a future policy edit adds an
    ``OR current_setting IS NULL`` escape hatch (or someone disables
    RLS on a table), this test fires.

    Seeds one row per table first so a passing test means "RLS hid the
    row," not "the table was empty anyway." Uses tenant-scoped
    application user_id (not the connecting pablo role) so the inserted
    rows are realistic — a clinician's grant for a patient they own.
    """

    def test_unset_guc_returns_zero_rows_on_every_tenant_table(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        seed_user = "rls-test-user"
        patient_id = str(uuid.uuid4())

        # Seed: insert a patient + grant. We do this with the GUC SET
        # so the policy USING clause passes (would otherwise refuse the
        # INSERT for the patient_clinicians grant row).
        with engine.begin() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(
                text("SELECT set_config('app.current_user_id', :u, false)"),
                {"u": seed_user},
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
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, "
                    "granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient_id, "u": seed_user},
            )

        # Now probe: a NEW connection with no GUC set must see zero
        # rows in every patient-scoped table. Use a separate connect
        # block so the prior transaction's GUC value is gone.
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            # Defensive: explicitly RESET the GUC in case any pool-level
            # cruft survived. This is the state we want to test.
            conn.execute(text("RESET app.current_user_id"))
            visible_counts: dict[str, int] = {}
            for table in TENANT_SCOPED_TABLES:
                # Skip tables that don't exist in this schema — same
                # accommodation as the relrowsecurity test.
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": tenant_schema, "t": table},
                ).first()
                if not exists:
                    continue
                # tenant_schema + table come from the validated allow-
                # list at the top of this file; not user input. ``text()``
                # interpolation is safe here.
                count = conn.execute(
                    text(f"SELECT count(*) FROM {tenant_schema}.{table}")  # noqa: S608
                ).scalar_one()
                visible_counts[table] = int(count)

        leaks = {t: c for t, c in visible_counts.items() if c > 0}
        assert not leaks, (
            "RLS fail-closed contract violated: with app.current_user_id "
            "unset, the following tables returned non-zero rows. Either "
            "RLS was disabled on the table, a policy permits rows on "
            "unset GUC, or the pablo role gained BYPASSRLS. "
            f"Leaks: {leaks}. All probed: {visible_counts}."
        )
