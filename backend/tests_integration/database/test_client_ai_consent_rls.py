# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof for client_ai_consent_events.

The table has a ``patient_id`` and no ``user_id``, so it takes the
``has_patient_access`` policy from ``enable_rls_on_schema`` with no bespoke
branch. This proves that policy actually fired on a freshly provisioned
practice schema, under a NOSUPERUSER NOBYPASSRLS role (see conftest.py):

1. Clinician A, who holds a grant, records "agreed" then "declined" and reads
   back both, in order, with A's name resolved and the later one current.
2. Clinician B, who holds no grant, reads nothing — after a control assertion
   that the rows are there for A, so the empty read is not vacuous.
3. B's raw INSERT is rejected by the policy's WITH CHECK.
4. The CHECK constraints refuse an unknown decision and an intake-form row
   with no submission.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine


_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_CLINICIAN_A = str(uuid.uuid4())
_CLINICIAN_B = str(uuid.uuid4())


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

    schema = f"practice_test_ai_consent_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def patient_id(engine: Engine, tenant_schema: str) -> str:
    """A client with a grant for clinician A only, and A on the platform."""
    pid = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO platform.users (id, email, name, created_at, status,"
                " is_platform_admin, chat_quality_review_opt_in,"
                " session_notes_quality_review_opt_in, inbox_quality_review_opt_in)"
                " VALUES (CAST(:u AS uuid), :email, 'Dr. A', now(), 'approved', false,"
                " false, false, false) ON CONFLICT (id) DO NOTHING"
            ),
            {"u": _CLINICIAN_A, "email": f"a-{pid}@example.com"},
        )
        _arm(conn, tenant_schema, _CLINICIAN_A)
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Test', 'Client', "
                "'test', 'client', 'active', 0, now(), now())"
            ),
            {"pid": pid},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": pid, "u": _CLINICIAN_A},
        )
    return pid


def _arm(conn: Connection, schema: str, user_id: str) -> None:
    conn.execute(text(f"SET search_path = {schema}, platform, public"))
    conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user_id})


def _repo_as(engine: Engine, schema: str, user_id: str) -> tuple[Any, Any, Any, Any]:
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from app.repositories.postgres.client_ai_consent import (  # noqa: PLC0415
        PostgresClientAiConsentRepository,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    schema_token = _current_tenant_schema.set(schema)
    uid_token = _current_user_id.set(user_id)
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {schema}, platform, public"))
    arm_current_user_id(session, user_id)
    return PostgresClientAiConsentRepository(session), session, schema_token, uid_token


def _release(session: Any, schema_token: Any, uid_token: Any) -> None:
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(schema_token)
    _current_user_id.reset(uid_token)


_INSERT = text(
    "INSERT INTO client_ai_consent_events "
    "(id, patient_id, decision, effective_on, source, recorded_by, recorded_at, "
    " intake_submission_id) "
    "VALUES (gen_random_uuid(), CAST(:pid AS uuid), :decision, current_date, :source, "
    "        CAST(:recorded_by AS uuid), now(), NULL)"
)


def _insert(conn: Connection, patient_id: str, user_id: str, **overrides: str | None) -> None:
    params: dict[str, str | None] = {
        "pid": patient_id,
        "decision": "consented",
        "source": "clinician",
        "recorded_by": user_id,
    }
    params.update(overrides)
    conn.execute(_INSERT, params)


def test_grantee_records_and_reads_history_in_order(
    engine: Engine, tenant_schema: str, patient_id: str
) -> None:
    from app.services.client_ai_consent import (  # noqa: PLC0415
        ai_consent_record,
        record_ai_consent,
    )

    repo, session, s_tok, u_tok = _repo_as(engine, tenant_schema, _CLINICIAN_A)
    try:
        record_ai_consent(
            patient_id, "consented", date(2026, 10, 1), "clinician", _CLINICIAN_A, repo=repo
        )
        record_ai_consent(
            patient_id, "declined", date(2026, 10, 2), "clinician", _CLINICIAN_A, repo=repo
        )
        session.commit()

        record = ai_consent_record(patient_id, repo=repo)
    finally:
        _release(session, s_tok, u_tok)

    assert record.current is not None
    assert record.current.decision == "declined"
    assert [(e.decision, e.effective_on) for e in record.history][-2:] == [
        ("consented", date(2026, 10, 1)),
        ("declined", date(2026, 10, 2)),
    ]
    assert record.current.recorded_by_name == "Dr. A"


def test_clinician_without_a_grant_reads_nothing(
    engine: Engine, tenant_schema: str, patient_id: str
) -> None:
    repo_a, sess_a, s_a, u_a = _repo_as(engine, tenant_schema, _CLINICIAN_A)
    try:
        from app.services.client_ai_consent import record_ai_consent  # noqa: PLC0415

        record_ai_consent(
            patient_id, "consented", date(2026, 10, 3), "clinician", _CLINICIAN_A, repo=repo_a
        )
        sess_a.commit()
        assert repo_a.list_for_patient(patient_id), "Control: A must see the rows"
    finally:
        _release(sess_a, s_a, u_a)

    repo_b, sess_b, s_b, u_b = _repo_as(engine, tenant_schema, _CLINICIAN_B)
    try:
        assert repo_b.list_for_patient(patient_id) == []
    finally:
        _release(sess_b, s_b, u_b)


def test_clinician_without_a_grant_cannot_write(
    engine: Engine, tenant_schema: str, patient_id: str
) -> None:
    with engine.connect() as conn:
        _arm(conn, tenant_schema, _CLINICIAN_B)
        with pytest.raises(ProgrammingError) as exc:
            _insert(conn, patient_id, _CLINICIAN_B)
        conn.rollback()

    assert "row-level security" in str(exc.value).lower()


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision": "maybe"},
        {"source": "intake_form"},
        {"recorded_by": None},
    ],
    ids=["unknown-decision", "intake-form-without-submission", "clinician-without-recorder"],
)
def test_check_constraints_refuse_malformed_rows(
    engine: Engine, tenant_schema: str, patient_id: str, overrides: dict[str, str | None]
) -> None:
    with engine.connect() as conn:
        _arm(conn, tenant_schema, _CLINICIAN_A)
        with pytest.raises(IntegrityError):
            _insert(conn, patient_id, _CLINICIAN_A, **overrides)
        conn.rollback()
