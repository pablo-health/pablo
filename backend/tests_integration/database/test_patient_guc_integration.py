# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration tests for the ``app.current_patient_id`` RLS GUC.

The patient principal is only worth anything if the GUC that carries it
behaves the way the clinician one does against a real Postgres:

* it is set for the transaction a patient request runs in;
* it survives the mid-request commit that releases locks, because the
  ``after_begin`` listener re-arms it (a ``set_config(..., is_local=true)``
  is cleared by every commit, so without the listener the next query would
  see an empty value and RLS would silently return nothing);
* it is gone once the request's context is torn down, so a pooled
  connection cannot carry one patient's identity into the next request;
* arming a patient does **not** arm the clinician GUC, which is what keeps
  every existing clinician-scoped policy closed to a patient principal.

Mirrors the shape of ``test_tenant_session_integration.py``. Runs only when
DATABASE_URL and DATABASE_BACKEND=postgres are set; ``tests_integration/
conftest.py`` provisions those via testcontainers when they are absent.
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

# Skip the whole module if no Postgres URL is available.
_DB_URL = os.environ.get("DATABASE_URL", "")
_SKIP = not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres"
pytestmark = pytest.mark.skipif(
    _SKIP,
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

os.environ.setdefault("MULTI_TENANCY_ENABLED", "false")

_PATIENT_A = "0b1d0f4c-4a7a-4d3f-9a1e-7c2b5f8e4d10"
_PATIENT_B = "9e7c2a18-3f5b-4c62-8d0a-1b6e9f3c7a24"
_CLINICIAN = "4bd6452f-45bf-53d0-9680-693205fde295"

# ---------------------------------------------------------------------------
# Imported after env vars are set so settings resolves correctly.
# ---------------------------------------------------------------------------

from app.db import (  # noqa: E402
    _current_patient_id,
    _current_user_id,
    arm_current_patient_id,
    arm_current_user_id,
    create_standalone_session,
    get_engine,
)
from app.db.tenant_session import tenant_db_session  # noqa: E402

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_pguc_{_SUFFIX}"


def _read(session, name: str) -> str:  # type: ignore[no-untyped-def]
    return session.execute(text(f"SELECT current_setting('{name}', true)")).scalar() or ""


def _reset_principal_gucs(session) -> None:  # type: ignore[no-untyped-def]
    """Clear both principal GUCs on this session's connection.

    Not paranoia about the product: several RLS-invariant modules in this
    suite set ``app.current_user_id`` with ``is_local=false``, which is
    *session*-scoped and therefore survives on the connection after it goes
    back to the pool. Checking one out here can hand us a stale clinician
    id, which would make the "arming a patient leaves the clinician GUC
    empty" assertions pass alone and fail in a full run. Resetting first
    keeps those assertions about what ``arm_current_patient_id`` does
    rather than about what the pool happens to be carrying.
    """
    session.execute(text("RESET app.current_user_id"))
    session.execute(text("RESET app.current_patient_id"))


@pytest.fixture
def _schema():  # type: ignore[return]
    session = create_standalone_session()
    session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}"))
    session.commit()
    session.close()
    yield _SCHEMA
    session = create_standalone_session()
    session.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    session.commit()
    session.close()


@pytest.fixture(autouse=True)
def _clean_principal_contextvars():  # type: ignore[return]
    """Start every test with no principal armed, and restore on the way out.

    Both vars, not just the patient one: these assertions are about which
    GUC gets armed, and an earlier module in the suite that left
    ``_current_user_id`` set would have the ``after_begin`` listener arm the
    clinician GUC on our sessions. That is test pollution rather than a
    product bug — ``DatabaseSessionMiddleware`` clears both at request end —
    but it makes the "arming a patient leaves the clinician GUC empty"
    assertion pass alone and fail in a full run.
    """
    patient_token = _current_patient_id.set(None)
    user_token = _current_user_id.set(None)
    yield
    _current_patient_id.reset(patient_token)
    _current_user_id.reset(user_token)


@pytest.mark.usefixtures("_schema")
class TestPatientGucArming:
    def test_guc_is_set_for_the_transaction(self) -> None:
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            assert _read(session, "app.current_patient_id") == _PATIENT_A
        finally:
            session.rollback()
            session.close()

    def test_arming_a_patient_leaves_the_clinician_guc_empty(self) -> None:
        """The fail-closed direction: clinician policies stay shut for a patient."""
        session = create_standalone_session(_SCHEMA)
        try:
            _reset_principal_gucs(session)
            arm_current_patient_id(session, _PATIENT_A)
            assert _read(session, "app.current_user_id") == ""
        finally:
            session.rollback()
            session.close()

    def test_arming_a_clinician_leaves_the_patient_guc_empty(self) -> None:
        """And the mirror: patient policies stay shut for a clinician."""
        session = create_standalone_session(_SCHEMA)
        try:
            _reset_principal_gucs(session)
            arm_current_user_id(session, _CLINICIAN)
            assert _read(session, "app.current_patient_id") == ""
        finally:
            session.rollback()
            session.close()

    def test_guc_survives_a_mid_request_commit(self) -> None:
        """The listener re-arms after the commit that releases the connection.

        ``set_config(..., is_local=true)`` is transaction-scoped, so the
        commit clears it and SQLAlchemy returns the connection to the pool.
        The next query begins a fresh transaction on a possibly different
        connection; ``_rearm_rls_principal_gucs_on_txn_begin`` is what puts
        the patient id back.
        """
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            assert _read(session, "app.current_patient_id") == _PATIENT_A

            session.commit()  # releases the connection; clears the xact-local GUC

            assert _read(session, "app.current_patient_id") == _PATIENT_A
        finally:
            session.rollback()
            session.close()

    def test_guc_is_absent_on_a_session_that_armed_nothing(self) -> None:
        """A reused connection must not carry a previous request's patient.

        The test owns one connection and binds both sessions to it, rather
        than arming a pooled session and hoping the pool hands the same
        backend back. Hoping is what this test used to do: it asserted
        ``pg_backend_pid()`` matched — a real guard, since a fresh backend
        obviously carries no GUC and would pass vacuously — but the pool
        makes no such promise, so under a full-suite run the checkout came
        back on a different backend and the guard failed the test rather
        than the product. Owning the connection turns the reuse into a
        fact, and makes the assertion strictly stronger: the same backend
        every time, not just when the pool happens to cooperate.
        """
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))

            armed = Session(bind=conn)
            arm_current_patient_id(armed, _PATIENT_A)
            assert _read(armed, "app.current_patient_id") == _PATIENT_A
            armed.rollback()
            armed.close()

            # Simulate DatabaseSessionMiddleware's teardown, then a fresh
            # request landing on this very connection. No manual RESET: the
            # assertion is only worth anything if it observes the connection
            # as the product leaves it. The patient GUC is only ever set
            # ``is_local=true``, so a rollback and a cleared ContextVar are
            # all it should take.
            _current_patient_id.set(None)

            fresh = Session(bind=conn)
            try:
                assert _read(fresh, "app.current_patient_id") == ""
            finally:
                fresh.rollback()
                fresh.close()

    def test_arming_a_patient_disarms_an_already_armed_clinician(self) -> None:
        """Mutual exclusion, enforced rather than assumed.

        The patient policies are PERMISSIVE, so Postgres ORs them with the
        clinician policies. A transaction carrying both GUCs satisfies both
        families at once and sees the union of clinician and patient
        grants. ``arm_current_user_id`` is called on the request-scoped
        session from several places outside ``get_tenant_context`` (the
        passkey route, the document-finalize and session-generation
        workers), so a patient surface reusing one of those would land
        both keys on one Session — and the ``after_begin`` listener re-arms
        whatever it finds.
        """
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_user_id(session, _CLINICIAN)
            assert _read(session, "app.current_user_id") == _CLINICIAN

            arm_current_patient_id(session, _PATIENT_A)

            assert _read(session, "app.current_patient_id") == _PATIENT_A
            assert _read(session, "app.current_user_id") == "", (
                "both principals armed on one transaction: the row set is the "
                "union of clinician and patient grants"
            )
        finally:
            session.rollback()
            session.close()

    def test_arming_a_clinician_disarms_an_already_armed_patient(self) -> None:
        """The mirror direction."""
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            arm_current_user_id(session, _CLINICIAN)

            assert _read(session, "app.current_user_id") == _CLINICIAN
            assert _read(session, "app.current_patient_id") == ""
        finally:
            session.rollback()
            session.close()

    def test_the_disarm_survives_a_commit(self) -> None:
        """Clearing must beat the listener's re-arm, not race it.

        The listener reads ``session.info`` first and the ContextVar as a
        fallback, so clearing only the dict would let the next transaction
        resurrect the disarmed principal from the ambient ContextVar.
        """
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_user_id(session, _CLINICIAN)
            arm_current_patient_id(session, _PATIENT_A)
            session.commit()

            assert _read(session, "app.current_patient_id") == _PATIENT_A
            assert _read(session, "app.current_user_id") == "", (
                "the clinician GUC came back after a commit — the disarm "
                "cleared session.info but not the ContextVar fallback"
            )
        finally:
            session.rollback()
            session.close()

    def test_both_principals_armed_at_once_is_refused(self) -> None:
        """The union of two principals' grants is never opened, it is raised on.

        ``arm_current_*`` clear each other's carriers, so this state has no
        legitimate producer — but they clear them on the ``Session`` they
        are handed, while the listener also reads the ambient ContextVars.
        A caller that armed a patient on the request session and then set
        the clinician ContextVar from the same context (entering
        ``tenant_db_session`` inline on the event loop instead of in a
        worker, against its documented contract) presents both.

        Arming both would OR the permissive patient policies with the
        clinician ones and hand the request the union — the single outcome
        the two-principal split exists to prevent. Arming neither would be
        a silent zero-row request, which reads as "no data" rather than as
        a bug. So: raise.
        """
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            # Simulate the contract violation: the clinician ContextVar set
            # behind the back of the session that is carrying a patient.
            _current_user_id.set(_CLINICIAN)
            session.commit()  # the next statement opens a fresh transaction

            with pytest.raises(RuntimeError, match="union of both principals"):
                session.execute(text("SELECT 1"))
        finally:
            # Undo the violation before anything else opens a transaction.
            # The autouse ContextVar fixture resets these too, but it runs
            # AFTER the schema fixture's teardown, and that teardown opens a
            # session of its own — which the guard would (correctly) refuse,
            # turning this test into an ERROR. The test manufactured the bad
            # state, so the test clears it.
            _current_user_id.set(None)
            _current_patient_id.set(None)
            session.rollback()
            session.close()

    def test_an_empty_patient_id_is_refused(self) -> None:
        """An empty id would arm as '' rather than NULL, changing policy semantics."""
        session = create_standalone_session(_SCHEMA)
        try:
            for bad in ("", "   "):
                with pytest.raises(ValueError, match="non-empty"):
                    arm_current_patient_id(session, bad)
            assert _read(session, "app.current_patient_id") == ""
        finally:
            session.rollback()
            session.close()

    def test_a_second_patient_replaces_the_first(self) -> None:
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            arm_current_patient_id(session, _PATIENT_B)
            session.commit()
            assert _read(session, "app.current_patient_id") == _PATIENT_B
        finally:
            session.rollback()
            session.close()


@pytest.mark.usefixtures("_schema")
class TestOffRequestWorkDoesNotInheritAPatient:
    def test_tenant_db_session_clears_the_patient_guc(self) -> None:
        """Background work opened from inside a patient request runs clinician-only.

        ``tenant_db_session`` is the clinician off-request primitive. Entered
        while a patient principal is armed in this context, it must not let
        the ``after_begin`` listener carry that patient's grants into the
        worker's session.
        """
        _current_patient_id.set(_PATIENT_A)

        with tenant_db_session(_SCHEMA, _CLINICIAN) as worker_session:
            assert _read(worker_session, "app.current_patient_id") == ""
            assert _read(worker_session, "app.current_user_id") == _CLINICIAN

    def test_the_outer_patient_is_restored_afterwards(self) -> None:
        """Clearing is scoped to the primitive, not a permanent stomp."""
        _current_patient_id.set(_PATIENT_A)

        with tenant_db_session(_SCHEMA, _CLINICIAN):
            pass

        assert _current_patient_id.get() == _PATIENT_A


# ---------------------------------------------------------------------------
# IDOR: can patient A reach patient B's row?
# ---------------------------------------------------------------------------


@pytest.fixture
def idor_table():  # type: ignore[return]
    """A patient-scoped canary table with the policy shape u37i.3 will ship.

    This is a PROOF OF MECHANISM, not the product's policies — no real
    patient-scoped table has an ``app.current_patient_id`` policy yet, and
    writing them is a separate change. What it proves is the thing worth
    knowing before those policies get written: that the GUC this PR adds is
    actually usable as an isolation boundary, that it fails closed when
    unset, and that a clinician principal does not satisfy it.

    Owner-bypass matters here. ``ENABLE ROW LEVEL SECURITY`` alone is not
    enough when the connecting role owns the table — which it does, since
    this connects as ``pablo`` and ``pablo`` runs the CREATE. Without
    ``FORCE``, every assertion below would pass vacuously by bypassing the
    policy rather than satisfying it.
    """
    engine = create_engine(_DB_URL)
    with engine.begin() as conn:
        # Guard against a vacuous suite: if the connecting role bypasses
        # RLS, every isolation assertion here is meaningless.
        bypasses = conn.execute(
            text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")
        ).scalar()
        if bypasses:
            pytest.skip("connecting role bypasses RLS; IDOR assertions would pass vacuously")

        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA}"))
        conn.execute(
            text(f"""
                CREATE TABLE IF NOT EXISTS {_SCHEMA}.companion_note (
                    id uuid PRIMARY KEY,
                    patient_id uuid NOT NULL,
                    body text NOT NULL
                )
            """)
        )
        conn.execute(text(f"ALTER TABLE {_SCHEMA}.companion_note ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {_SCHEMA}.companion_note FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(f"""
                CREATE POLICY companion_note_patient_scope
                ON {_SCHEMA}.companion_note
                USING (patient_id::text = current_setting('app.current_patient_id', true))
            """)
        )

    # Seed one row per patient, with RLS temporarily off so the seed itself
    # is not the thing under test.
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {_SCHEMA}.companion_note NO FORCE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {_SCHEMA}.companion_note DISABLE ROW LEVEL SECURITY"))
        for patient, body in ((_PATIENT_A, "note for A"), (_PATIENT_B, "note for B")):
            conn.execute(
                text(
                    f"INSERT INTO {_SCHEMA}.companion_note (id, patient_id, body) "  # noqa: S608
                    "VALUES (gen_random_uuid(), CAST(:pid AS uuid), :body)"
                ),
                {"pid": patient, "body": body},
            )
        conn.execute(text(f"ALTER TABLE {_SCHEMA}.companion_note ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {_SCHEMA}.companion_note FORCE ROW LEVEL SECURITY"))

    yield

    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
    engine.dispose()


@pytest.mark.usefixtures("idor_table")
class TestPatientCannotReachAnotherPatientsRow:
    """The direct-object-reference attempt, at the layer that must stop it.

    A patient authenticated as A asks for B's data — by listing, by naming
    B's id outright, and by trying to write to B's row. Every one must come
    back empty rather than forbidden: RLS filters, it does not raise, so
    "zero rows" is the correct shape of the refusal and also means an
    attacker learns nothing about whether B's row exists.
    """

    def test_the_seed_is_visible_to_its_owner(self) -> None:
        """Guard against the whole class passing because the table is empty."""
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            rows = (
                session.execute(
                    text(f"SELECT body FROM {_SCHEMA}.companion_note")  # noqa: S608
                )
                .scalars()
                .all()
            )
            assert rows == ["note for A"]
        finally:
            session.rollback()
            session.close()

    def test_listing_never_includes_the_other_patient(self) -> None:
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            rows = (
                session.execute(
                    text(f"SELECT body FROM {_SCHEMA}.companion_note")  # noqa: S608
                )
                .scalars()
                .all()
            )
            assert "note for B" not in rows
        finally:
            session.rollback()
            session.close()

    def test_naming_the_other_patients_id_outright_returns_nothing(self) -> None:
        """The actual IDOR move: armed as A, ask for B by id."""
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            rows = (
                session.execute(
                    text(
                        f"SELECT body FROM {_SCHEMA}.companion_note "  # noqa: S608
                        "WHERE patient_id = CAST(:pid AS uuid)"
                    ),
                    {"pid": _PATIENT_B},
                )
                .scalars()
                .all()
            )
            assert rows == []
        finally:
            session.rollback()
            session.close()

    def test_writing_to_the_other_patients_row_affects_nothing(self) -> None:
        """Reads are not the only IDOR surface; a blind write must miss too."""
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            result = session.execute(
                text(
                    f"UPDATE {_SCHEMA}.companion_note SET body = 'tampered' "  # noqa: S608
                    "WHERE patient_id = CAST(:pid AS uuid)"
                ),
                {"pid": _PATIENT_B},
            )
            assert result.rowcount == 0
            session.commit()
        finally:
            session.rollback()
            session.close()

        # And B's row is intact when B asks for it.
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_B)
            rows = (
                session.execute(
                    text(f"SELECT body FROM {_SCHEMA}.companion_note")  # noqa: S608
                )
                .scalars()
                .all()
            )
            assert rows == ["note for B"]
        finally:
            session.rollback()
            session.close()

    def test_deleting_the_other_patients_row_affects_nothing(self) -> None:
        session = create_standalone_session(_SCHEMA)
        try:
            arm_current_patient_id(session, _PATIENT_A)
            result = session.execute(
                text(
                    f"DELETE FROM {_SCHEMA}.companion_note "  # noqa: S608
                    "WHERE patient_id = CAST(:pid AS uuid)"
                ),
                {"pid": _PATIENT_B},
            )
            assert result.rowcount == 0
            session.commit()
        finally:
            session.rollback()
            session.close()

    def test_no_principal_armed_sees_nothing(self) -> None:
        """Fail-closed: an unarmed session is not an admin session."""
        session = create_standalone_session(_SCHEMA)
        try:
            _reset_principal_gucs(session)
            rows = (
                session.execute(
                    text(f"SELECT body FROM {_SCHEMA}.companion_note")  # noqa: S608
                )
                .scalars()
                .all()
            )
            assert rows == []
        finally:
            session.rollback()
            session.close()

    def test_a_clinician_principal_does_not_satisfy_a_patient_policy(self) -> None:
        """Cross-principal: arming the clinician GUC must not open patient rows.

        This is the database-level half of the separation the dependency
        enforces at the door, and the reason the two ids get two GUCs
        instead of sharing one.
        """
        session = create_standalone_session(_SCHEMA)
        try:
            _reset_principal_gucs(session)
            arm_current_user_id(session, _CLINICIAN)
            rows = (
                session.execute(
                    text(f"SELECT body FROM {_SCHEMA}.companion_note")  # noqa: S608
                )
                .scalars()
                .all()
            )
            assert rows == []
        finally:
            session.rollback()
            session.close()

    def test_a_clinician_id_equal_to_a_patient_id_still_sees_nothing(self) -> None:
        """The collision case that a single shared GUC would have allowed.

        Both ids are uuids drawn from the same space. If the policy read one
        "current principal" GUC, a clinician whose user id happened to equal
        a patient id would read that patient's rows. Two GUCs make the
        collision unrepresentable — this pins that.
        """
        session = create_standalone_session(_SCHEMA)
        try:
            _reset_principal_gucs(session)
            arm_current_user_id(session, _PATIENT_B)  # clinician id == patient B's id
            rows = (
                session.execute(
                    text(f"SELECT body FROM {_SCHEMA}.companion_note")  # noqa: S608
                )
                .scalars()
                .all()
            )
            assert rows == []
        finally:
            session.rollback()
            session.close()
