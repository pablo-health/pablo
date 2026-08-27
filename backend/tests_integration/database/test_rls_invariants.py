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
#
# Curated floor: tables that must be covered regardless of their column shape
# (e.g. chat_messages has neither user_id nor patient_id directly; patients /
# patient_clinicians / patient_documents are by-name special cases).
# Add new non-derivable tenant tables here.
#
# The union below auto-covers every RLS-forced table (patient-access,
# user-owned, or special-cased) derived from the ORM via
# rls_forced_tenant_tables() — so a new table of any RLS-bearing column
# shape is covered automatically with zero hand edits here.
TENANT_SCOPED_TABLES = (
    "patients",
    "patient_clinicians",
    "chat_conversations",
    "chat_messages",
    "notes",
    "outcome_measures",
    "therapy_sessions",
    "appointments",
    "patient_documents",
    "supervision_relationships",
    "supervision_hours",
)

# Self-healing coverage: union the curated list with every RLS-forced table
# derived automatically from the ORM.  Any new table with user_id, patient_id,
# or id column (and not in not_row_scoped) is picked up here automatically.
from app.db import PATIENT_READABLE_TABLES, rls_forced_tenant_tables  # noqa: E402

_EFFECTIVE_TABLES: frozenset[str] = frozenset(TENANT_SCOPED_TABLES) | rls_forced_tenant_tables()


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
                    {"schema": tenant_schema, "tables": list(_EFFECTIVE_TABLES)},
                )
                .mappings()
                .all()
            )

        by_table = {r["relname"]: r for r in rows}
        missing = [t for t in _EFFECTIVE_TABLES if t not in by_table]
        # Some tables (appointments, therapy_sessions) may not exist yet
        # in every schema variant. Don't fail on absence — fail only
        # when the table exists but RLS posture is wrong. Surface what
        # was checked so a reader can confirm the coverage is what they
        # expect.
        checked = sorted(by_table.keys())
        assert checked, (
            f"None of {_EFFECTIVE_TABLES} exist in {tenant_schema}; schema provisioning is broken."
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
        seed_user = "30e13250-a1bb-5d5e-991d-c74ac69e26e3"
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
            for table in _EFFECTIVE_TABLES:
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


class TestPatientPrincipalHasNoClinicianReach:
    """A patient principal must see nothing in the clinician-scoped tables.

    This is the same fail-closed probe as the class above, run against the
    same REAL provisioned tenant schema and the REAL policies that ship —
    but with the patient GUC armed instead of the clinician one.

    Why it matters, and why it is not redundant with the class above: the
    tables here are scoped by ``app.current_user_id``. Arming a *patient*
    leaves that GUC unset, so every one of them must stay closed. If a
    future change ever made the two principals share a GUC — or made
    ``arm_current_patient_id`` also arm the clinician one "for
    convenience" — a patient session would silently acquire a clinician's
    reach across the whole tenant, and this test is what fires.

    Note what this does NOT prove: no product table has an
    ``app.current_patient_id`` policy yet, so nothing here shows patient
    data being correctly scoped *to its own patient*. That arrives with
    the patient-scoped policies; the two-patient IDOR proof against a
    canary table lives in ``test_patient_guc_integration.py`` until then.
    """

    def test_patient_guc_alone_returns_zero_rows_on_every_clinician_table(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        seed_user = "8f4c1b7a-2d9e-5a3c-b6f1-0e2d4c8a9b31"
        patient_id = str(uuid.uuid4())

        # Seed as a clinician so a passing test means "RLS hid the row",
        # not "the table was empty anyway".
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
                    "VALUES (CAST(:pid AS uuid), 'Grace', 'Hopper', "
                    "'grace', 'hopper', 'active', 0, now(), now())"
                ),
                {"pid": patient_id},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:pid AS uuid), :u, :u)"
                ),
                {"pid": patient_id, "u": seed_user},
            )

        # Probe on a fresh connection: patient armed, clinician explicitly
        # cleared. This is exactly the state a patient request runs in.
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            conn.execute(text("RESET app.current_user_id"))
            conn.execute(
                text("SELECT set_config('app.current_patient_id', :p, false)"),
                {"p": patient_id},
            )

            # Guard against a vacuous pass: the patient GUC must really be
            # armed, or "zero rows everywhere" proves nothing.
            armed = conn.execute(
                text("SELECT current_setting('app.current_patient_id', true)")
            ).scalar()
            assert armed == patient_id

            visible_counts: dict[str, int] = {}
            for table in _EFFECTIVE_TABLES:
                # The tables a patient is deliberately entitled to read are
                # not part of this assertion — they have their own patient
                # arm, and their two-patient isolation is proven in
                # test_patient_principal_rls.py. What is under test here is
                # everything the patient was NOT granted.
                if table in PATIENT_READABLE_TABLES:
                    continue
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_schema = :s AND table_name = :t"
                    ),
                    {"s": tenant_schema, "t": table},
                ).first()
                if not exists:
                    continue
                count = conn.execute(
                    text(f"SELECT count(*) FROM {tenant_schema}.{table}")  # noqa: S608
                ).scalar_one()
                visible_counts[table] = int(count)

            # Control: the patient CAN see their own row in the one table
            # they are registered for. Without this the zeros above could
            # mean "the patient GUC does nothing at all" rather than "the
            # patient GUC grants exactly what it should".
            own = conn.execute(
                text(f"SELECT count(*) FROM {tenant_schema}.patients"),  # noqa: S608
            ).scalar_one()
            assert int(own) == 1, (
                "the patient could not see their own patients row, so the "
                "zero-rows assertions below prove nothing about scoping"
            )

        assert visible_counts, "no clinician-scoped tables were probed"
        leaks = {t: c for t, c in visible_counts.items() if c > 0}
        assert not leaks, (
            "A patient principal reached clinician-scoped rows. With only "
            "app.current_patient_id armed, every table NOT registered in "
            "PATIENT_READABLE_TABLES must return zero rows. Either the two "
            "principals now share a GUC, arm_current_patient_id also arms "
            "the clinician GUC, or a policy accepts the patient GUC. "
            f"Leaks: {leaks}. All probed: {visible_counts}."
        )


class TestRlsGucRearmedAcrossCommit:
    """The ``after_begin`` Session listener must re-arm ``app.current_user_id``
    on every new transaction.

    ``set_config(..., is_local=true)`` is transaction-scoped, so a mid-request
    commit (e.g. the lock-release commit before SOAP generation) clears it.
    Without ``_rearm_rls_principal_gucs_on_txn_begin``, the next query in the request
    would start a fresh transaction with no GUC and RLS would silently return
    zero rows — patient data vanishing mid-request, indistinguishable from
    "no data". That listener had no test coverage.
    """

    def test_guc_survives_mid_session_commit(self, engine: Engine, tenant_schema: str) -> None:
        from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

        seed_user = "a2bb584b-9a12-5fa0-ab07-6b8edf1aef38"
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


class TestRlsGucRearmedAcrossCommitOnSyncRoute:
    """The mid-request-commit re-arm must work even when the request
    ContextVar is gone — the sync-route case.

    Sync routes (``def`` endpoints) and their sync dependencies each run
    in a *separate* anyio threadpool worker, and ``run_in_threadpool``
    copies the event-loop context into a throwaway worker per call. So a
    ``ContextVar.set()`` performed by ``arm_current_user_id`` inside the
    sync auth dependency is discarded when that worker returns — the
    endpoint (which calls ``_commit_intermediate`` before the SOAP LLM
    call) later runs in a *different* worker whose context copy never saw
    the set, leaving ``_current_user_id`` at ``None``. The previous
    ContextVar-only re-arm therefore no-oped on every post-commit
    transaction, and under NOBYPASSRLS the note read/write and the
    FAILED-status update saw zero rows / failed ``WITH CHECK``
    (``POST /api/patients/{id}/sessions/upload`` -> 500).

    The fix arms the user id on ``session.info`` — which rides the shared
    Session object across both workers — so the ``after_begin`` listener
    re-arms regardless of the ContextVar. ``TestRlsGucRearmedAcrossCommit``
    above covers the async (ContextVar present) path; this test isolates
    the sync path by arming and then dropping the ContextVar, emulating
    the threadpool-worker boundary.
    """

    def test_guc_survives_mid_session_commit_without_contextvar(
        self, engine: Engine, tenant_schema: str
    ) -> None:
        from app.db import (  # noqa: PLC0415
            _RLS_USER_ID_KEY,
            _current_tenant_schema,
            _current_user_id,
            arm_current_user_id,
        )

        seed_user = "2c98d066-9e2a-5e08-af2f-35aa21088b4a"
        patient_id = str(uuid.uuid4())

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

        # Only the search_path ContextVar is set (the pool-checkout listener
        # needs it). Critically, _current_user_id is left UNSET — this is the
        # state the endpoint worker sees, because the auth dependency armed it
        # in a different, now-discarded worker context.
        schema_token = _current_tenant_schema.set(tenant_schema)
        session = OrmSession(bind=engine)
        try:
            session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))

            # Auth-dependency phase: arm on the request session. This stashes
            # the id on session.info AND set_config's the open txn.
            arm_current_user_id(session, seed_user)
            assert session.info[_RLS_USER_ID_KEY] == seed_user

            # Emulate the threadpool-worker boundary: the ContextVar mutation
            # arm_current_user_id just made does not survive into the endpoint
            # worker's context copy. session.info, riding the Session object,
            # does.
            _current_user_id.set(None)

            before = session.execute(count_sql, {"pid": patient_id}).scalar_one()
            assert before == 1, "patient should be visible right after arming"

            # Endpoint phase: mid-request commit (lock release before the LLM
            # call) clears the xact-local GUC; the next query begins a fresh
            # txn whose after_begin must re-arm from session.info.
            session.commit()
            after = session.execute(count_sql, {"pid": patient_id}).scalar_one()
            assert after == 1, (
                "RLS GUC was not re-armed from session.info after a mid-request "
                "commit on a sync route (ContextVar absent) — this is the "
                "upload-session 500 regression."
            )

            # Control: drop session.info too. With neither source the listener
            # no-ops, the GUC stays empty, and RLS hides the row — proving the
            # assertion above is real enforcement, not a vacuous pass.
            session.commit()
            session.info.pop(_RLS_USER_ID_KEY, None)
            without_any = session.execute(count_sql, {"pid": patient_id}).scalar_one()
            assert without_any == 0, (
                "With neither session.info nor the ContextVar set, the patient "
                "must be hidden; a non-zero count means RLS is not enforcing and "
                "the re-arm assertion above is vacuous."
            )
        finally:
            session.close()
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
                    {"u": "982676a4-6fbb-5618-b331-8765ebd04146", "p": "practice-x"},
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

        clinician = "469f0156-becc-56ef-802b-182c74cab9e6"
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


class TestOverlayNotRowScopedRegistry:
    """A deployment can register extra non-row-scoped tenant tables.

    ``enable_rls_on_schema`` refuses to leave a force-RLS'd tenant table
    with no policy (a silent deny-all). A deployment-specific tenant
    table that carries only an ``id`` column — its isolation boundary is
    the tenant schema, not a per-row predicate — would trip that guard.
    ``register_overlay_not_row_scoped`` lets the deployment opt such a
    table into the same RLS-off path as the built-in ``ehr_routes`` /
    ``users`` entries.
    """

    @pytest.fixture
    def bare_schema(self, engine: Engine) -> Iterator[str]:
        """A throwaway schema holding a single id-only table.

        Built by hand (not through provisioning) so the only scoping
        column present is ``id`` — the exact shape that has no policy
        branch and would otherwise raise.
        """
        schema = f"practice_test_overlay_{uuid.uuid4().hex[:8]}"
        with engine.connect() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            conn.execute(
                text(
                    f'CREATE TABLE "{schema}".overlay_only_table '
                    "(id uuid PRIMARY KEY, created_at timestamptz DEFAULT now())"
                )
            )
            conn.commit()
        yield schema
        with engine.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            conn.commit()

    @staticmethod
    def _rls_state(engine: Engine, schema: str, table: str) -> tuple[bool, bool]:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT c.relrowsecurity, c.relforcerowsecurity "
                        "FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relname = :table"
                    ),
                    {"schema": schema, "table": table},
                )
                .mappings()
                .one()
            )
        return row["relrowsecurity"], row["relforcerowsecurity"]

    def test_unregistered_id_only_table_raises(self, engine: Engine, bare_schema: str) -> None:
        from app.db import enable_rls_on_schema  # noqa: PLC0415

        with OrmSession(bind=engine) as session, pytest.raises(RuntimeError) as exc:
            enable_rls_on_schema(session, bare_schema)
        assert "no RLS policy defined" in str(exc.value), (
            "An id-only tenant table with no policy branch and no "
            "registration must raise the deny-all guard."
        )

    def test_registered_table_is_treated_as_not_row_scoped(
        self, engine: Engine, bare_schema: str
    ) -> None:
        from app.db import (  # noqa: PLC0415
            _OVERLAY_NOT_ROW_SCOPED,
            enable_rls_on_schema,
            register_overlay_not_row_scoped,
        )

        register_overlay_not_row_scoped("overlay_only_table")
        try:
            # (a) No RuntimeError — the guard now treats it as not-row-scoped.
            with OrmSession(bind=engine) as session:
                enable_rls_on_schema(session, bare_schema)

            # (b) Same RLS state as a built-in not_row_scoped table:
            # ``enable_rls_on_schema`` leaves RLS disabled on those.
            rowsec, forcesec = self._rls_state(engine, bare_schema, "overlay_only_table")
            assert rowsec is False, (
                "A registered not-row-scoped table must have RLS left "
                "disabled (same as ehr_routes/users), not force-enabled."
            )
            assert forcesec is False
        finally:
            _OVERLAY_NOT_ROW_SCOPED.discard("overlay_only_table")
