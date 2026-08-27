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
from sqlalchemy import text

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
        """A pooled connection must not carry a previous request's patient."""
        armed = create_standalone_session(_SCHEMA)
        arm_current_patient_id(armed, _PATIENT_A)
        assert _read(armed, "app.current_patient_id") == _PATIENT_A
        armed.rollback()
        armed.close()

        # Simulate DatabaseSessionMiddleware's teardown, then a fresh request.
        _current_patient_id.set(None)

        # No reset here, deliberately: this assertion is only worth anything
        # if it observes the connection as the product leaves it. The patient
        # GUC is only ever set ``is_local=true``, so a rollback and a cleared
        # ContextVar are all it should take.
        fresh = create_standalone_session(_SCHEMA)
        try:
            assert _read(fresh, "app.current_patient_id") == ""
        finally:
            fresh.rollback()
            fresh.close()

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
