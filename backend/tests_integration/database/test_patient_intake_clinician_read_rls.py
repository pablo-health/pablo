# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinician read of intake submissions, proven against real Postgres.

``test_patient_intake_submissions_rls.py`` proves the table's policies. What
is new here is the repository method the chart calls:
``list_for_clinician``, which asks ``has_patient_access`` before it selects.
Two checks stand between a stranger and these rows — the grant lookup in the
repository and the RLS policy under it — and this file exercises the pair as
the route reaches them, on a session armed the way the request arms one.

Runs as the ``pablo`` role, which the integration conftest creates
``NOSUPERUSER NOBYPASSRLS`` exactly as production has it, so neither check
is quietly bypassed. Every empty-result assertion is preceded by a control
on the same schema, so nothing can pass because the table was empty.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

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

_TREATING_CLINICIAN = "6c2f9a41-8b3d-5e7a-9f04-1d8c3b6e2a95"
_STRANGER_CLINICIAN = "b1d0f47e-2c95-5a83-9e61-4f07a2c8d316"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_intake_read_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient_with_two_submissions(engine: Engine, tenant_schema: str) -> str:
    """One patient the treating clinician holds a grant on, and two forms.

    The submissions are written on a patient-armed connection, the way the
    submit route writes them — so if the patient write arm were missing this
    fixture would fail rather than leave an empty table behind.
    """
    patient_id = str(uuid.uuid4())

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _TREATING_CLINICIAN},
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
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _TREATING_CLINICIAN},
        )

    now = datetime.now(UTC).replace(microsecond=0)
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("RESET app.current_user_id"))
        conn.execute(
            text("SELECT set_config('app.current_patient_id', :p, false)"),
            {"p": patient_id},
        )
        for submission_id, minutes_ago in (("intake-older", 60), ("intake-newer", 5)):
            conn.execute(
                text(
                    "INSERT INTO patient_intake_submissions "
                    "(id, patient_id, submitted_at, payload, created_by, created_at) "
                    "VALUES (:id, CAST(:pid AS uuid), :at, CAST(:payload AS jsonb), :by, :at)"
                ),
                {
                    "id": submission_id,
                    "pid": patient_id,
                    "at": now - timedelta(minutes=minutes_ago),
                    "payload": '{"reason_text": "seed", "name_confirmed": true}',
                    "by": patient_id,
                },
            )

    return patient_id


def _repo_as(engine: Engine, schema: str, user_id: str):  # type: ignore[no-untyped-def]
    """The real repository on a session armed for *user_id*, as a request arms it."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.patient_intake_submission import (  # noqa: PLC0415
        PostgresPatientIntakeSubmissionRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    session.execute(text("RESET app.current_patient_id"))
    arm_current_user_id(session, user_id)
    return PostgresPatientIntakeSubmissionRepository(session), session, schema_token, uid_token


def _close(session, schema_token, uid_token) -> None:  # type: ignore[no-untyped-def]
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)
    _current_user_id.reset(uid_token)


class TestRoleReallyEnforcesRls:
    """If this fails, every isolation assertion in this file is meaningless."""

    def test_connecting_role_does_not_bypass_rls(self, engine: Engine) -> None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).first()
        assert row is not None
        assert not row[0], "connecting role is a superuser; RLS would be bypassed"
        assert not row[1], "connecting role has BYPASSRLS; RLS would be bypassed"


class TestClinicianRead:
    def test_treating_clinician_reads_both_newest_first(
        self, engine: Engine, tenant_schema: str, patient_with_two_submissions: str
    ) -> None:
        """The control. Everything below is only meaningful against this."""
        repo, session, schema_token, uid_token = _repo_as(
            engine, tenant_schema, _TREATING_CLINICIAN
        )
        try:
            rows = repo.list_for_clinician(patient_with_two_submissions, _TREATING_CLINICIAN)
        finally:
            _close(session, schema_token, uid_token)

        assert [row["id"] for row in rows] == ["intake-newer", "intake-older"]

    def test_stranger_clinician_reads_nothing(
        self, engine: Engine, tenant_schema: str, patient_with_two_submissions: str
    ) -> None:
        """Same practice, same schema, no grant on this patient."""
        repo, session, schema_token, uid_token = _repo_as(
            engine, tenant_schema, _STRANGER_CLINICIAN
        )
        try:
            rows = repo.list_for_clinician(patient_with_two_submissions, _STRANGER_CLINICIAN)
        finally:
            _close(session, schema_token, uid_token)

        assert rows == []

    def test_the_policy_refuses_the_stranger_even_without_the_grant_check(
        self, engine: Engine, tenant_schema: str, patient_with_two_submissions: str
    ) -> None:
        """Defense in depth: drop the repository's check and RLS still holds.

        The repository asks ``has_patient_access`` first, which is what
        returns the empty list above. This goes around that question and
        selects the rows outright on a stranger-armed session, so the answer
        can only come from the policy.
        """
        from app.db import arm_current_user_id  # noqa: PLC0415
        from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

        query = text(
            "SELECT id FROM patient_intake_submissions WHERE patient_id = CAST(:p AS uuid)"
        )
        with OrmSession(bind=engine) as session:
            session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
            session.execute(text("RESET app.current_patient_id"))

            arm_current_user_id(session, _TREATING_CLINICIAN)
            visible = set(
                session.execute(query, {"p": patient_with_two_submissions}).scalars().all()
            )
            assert visible == {"intake-newer", "intake-older"}

            arm_current_user_id(session, _STRANGER_CLINICIAN)
            hidden = set(
                session.execute(query, {"p": patient_with_two_submissions}).scalars().all()
            )
            assert hidden == set()

    def test_an_unknown_patient_is_an_empty_list(self, engine: Engine, tenant_schema: str) -> None:
        """A patient id nobody holds is the same answer as no submissions."""
        repo, session, schema_token, uid_token = _repo_as(
            engine, tenant_schema, _TREATING_CLINICIAN
        )
        try:
            rows = repo.list_for_clinician(str(uuid.uuid4()), _TREATING_CLINICIAN)
        finally:
            _close(session, schema_token, uid_token)

        assert rows == []
