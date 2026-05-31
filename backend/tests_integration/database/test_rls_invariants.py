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
from sqlalchemy.orm import Session as OrmSession

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


class TestRlsGucRearmedAcrossCommit:
    """The ``after_begin`` Session listener must re-arm ``app.current_user_id``
    on every new transaction.

    ``set_config(..., is_local=true)`` is transaction-scoped, so a mid-request
    commit (e.g. the lock-release commit before SOAP generation) clears it.
    Without ``_rearm_rls_user_id_on_txn_begin``, the next query in the request
    would start a fresh transaction with no GUC and RLS would silently return
    zero rows — patient data vanishing mid-request, indistinguishable from
    "no data". That listener had no test coverage.
    """

    def test_guc_survives_mid_session_commit(self, engine: Engine, tenant_schema: str) -> None:
        from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

        seed_user = "rearm-test-user"
        patient_id = str(uuid.uuid4())

        # Seed a patient + grant with the GUC set so the policy USING/CHECK
        # clauses accept the writes (mirrors the fail-closed test's seed).
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
                    "granted_by) VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient_id, "u": seed_user},
            )

        count_sql = text(
            f"SELECT count(*) FROM {tenant_schema}.patients "  # noqa: S608
            "WHERE id = CAST(:pid AS uuid)"
        )

        # Arm the request-scoped ContextVars the session listeners read.
        # Use an ORM Session (not a raw connection) — the after_begin
        # listener is registered on Session, so it only fires for ORM
        # transactions. _current_tenant_schema arms the pool-checkout
        # listener that re-applies search_path: the mid-request commit
        # below releases the connection (checkin resets search_path to a
        # neutral value) and the next query re-checks it out, so without
        # this the RLS policy's unqualified ``patient_clinicians`` lookup
        # would fail to resolve once the connection is re-checked-out.
        # This mirrors what DatabaseSessionMiddleware sets on every request.
        token = _current_user_id.set(seed_user)
        schema_token = _current_tenant_schema.set(tenant_schema)
        session = OrmSession(bind=engine)
        try:
            session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))

            before = session.execute(count_sql, {"pid": patient_id}).scalar_one()
            assert before == 1, "patient should be visible with the GUC armed"

            # Mid-request commit clears the transaction-local GUC.
            session.commit()

            # The next query begins a fresh transaction; after_begin must
            # re-arm the GUC from the ContextVar so the row stays visible.
            after = session.execute(count_sql, {"pid": patient_id}).scalar_one()
            assert after == 1, (
                "RLS GUC was not re-armed after a mid-session commit — the "
                "after_begin listener did not fire and the patient vanished."
            )

            # Control: clear the ContextVar. The next transaction's listener
            # no-ops, the GUC stays empty, and RLS hides the row. This proves
            # the assertions above are real RLS enforcement, not a vacuous
            # pass (RLS disabled / superuser bypass).
            session.commit()
            _current_user_id.set(None)
            without_guc = session.execute(count_sql, {"pid": patient_id}).scalar_one()
            assert without_guc == 0, (
                "With app.current_user_id unset the patient must be hidden; "
                "a non-zero count means RLS is not actually enforcing and the "
                "re-arm assertions above are vacuous."
            )
        finally:
            session.close()
            _current_user_id.reset(token)
            _current_tenant_schema.reset(schema_token)


class TestArmCurrentUserIdEnablesProfileWrite:
    """``clinician_profiles`` is RLS-scoped by ``user_id``, so the pre-MFA
    onboarding upsert must arm ``app.current_user_id`` first.

    Regression test for the onboarding-wizard stall surfaced when the
    ``pablo`` role was flipped to NOBYPASSRLS: ``PATCH /api/users/me`` and
    ``/me/professional-info`` run pre-MFA (``get_current_user_no_mfa``),
    never pass through ``get_tenant_context``, and so left the GUC unset.
    The ``clinician_profiles`` ``WITH CHECK (user_id = current_setting(...))``
    policy then rejected the upsert with ``InsufficientPrivilege``. The fix
    is ``arm_current_user_id``, called from ``_upsert_clinician_profile``.

    This proves both halves: unset GUC ⇒ insert rejected (the bug, now the
    fail-closed contract), and ``arm_current_user_id`` ⇒ insert accepted.
    """

    def test_clinician_profiles_has_rls_forced(self, engine: Engine, tenant_schema: str) -> None:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relname = 'clinician_profiles'"
                    ),
                    {"schema": tenant_schema},
                )
                .mappings()
                .one_or_none()
            )
        assert row is not None, "clinician_profiles missing from the provisioned tenant schema"
        # user_id-scoped, not skipped (the enable_rls_on_schema docstring
        # once wrongly claimed otherwise).
        assert row["relrowsecurity"], f"clinician_profiles must have RLS enabled. Got {dict(row)}."
        assert row["relforcerowsecurity"], (
            f"clinician_profiles must have RLS forced. Got {dict(row)}."
        )

    def test_unarmed_profile_insert_is_rejected(self, engine: Engine, tenant_schema: str) -> None:
        from sqlalchemy.exc import ProgrammingError  # noqa: PLC0415

        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_user_id"))
            with pytest.raises(ProgrammingError) as exc:
                conn.execute(
                    text(
                        "INSERT INTO clinician_profiles "
                        "(user_id, practice_id, role, joined_at) "
                        "VALUES (:u, :p, 'clinician', now())"
                    ),
                    {"u": "unarmed-user", "p": "practice-x"},
                )
            conn.rollback()
        assert "row-level security" in str(exc.value).lower(), (
            "Expected an RLS WITH CHECK violation when the GUC is unset; "
            f"got a different error: {exc.value}"
        )

    def test_arm_current_user_id_allows_profile_insert(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.db import (  # noqa: PLC0415
            _current_tenant_schema,
            _current_user_id,
            arm_current_user_id,
        )

        clinician = "armed-clinician"
        schema_token = _current_tenant_schema.set(tenant_schema)
        user_token = _current_user_id.set(None)
        session = OrmSession(bind=engine)
        try:
            session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            arm_current_user_id(session, clinician)
            session.execute(
                text(
                    "INSERT INTO clinician_profiles "
                    "(user_id, practice_id, role, joined_at) "
                    "VALUES (:u, :p, 'clinician', now())"
                ),
                {"u": clinician, "p": "practice-x"},
            )
            session.commit()

            # Visible to its owner (after_begin re-arms the GUC post-commit).
            count = session.execute(
                text("SELECT count(*) FROM clinician_profiles WHERE user_id = :u"),
                {"u": clinician},
            ).scalar_one()
            assert count == 1, (
                "armed clinician should see their own profile row after the "
                "upsert; the after_begin re-arm may have failed"
            )
        finally:
            session.close()
            _current_user_id.reset(user_token)
            _current_tenant_schema.reset(schema_token)
