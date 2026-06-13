# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for the dashboard-summary repository queries.

The dashboard summary route is backed by three repository reads added with it:

* ``PostgresTherapySessionRepository.count_by_status`` — GROUP BY status over
  the caller's accessible, non-deleted sessions,
* ``PostgresTherapySessionRepository.list_recent_by_status`` — the most-recent
  N sessions in one status, and
* ``PostgresNotesRepository.count_unfinalized`` — session-attached notes
  (``session_id IS NOT NULL``) still awaiting signature.

The route's unit test drives these against in-memory repositories, which don't
enforce the join through ``patient_clinicians``, the grant filters, the GROUP
BY, or the ``ORDER BY``/``LIMIT`` — the SQL-shape failures a fake collaborator
hides (the chat-500 / ``has_patient_access`` bind-param bug class). These tests
close that gap against a provisioned tenant schema, using the per-request
context the app arms: ``set_tenant_schema`` for ``search_path`` and
``arm_current_user_id`` for the ``app.current_user_id`` RLS GUC. Because the
test role is NOBYPASSRLS, the cross-grant assertions prove both the repository's
own grant filter and the database RLS policy.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.db import (
    _current_tenant_schema,
    arm_current_user_id,
    set_tenant_schema,
)
from app.models import Patient, SessionStatus, TherapySession, Transcript
from app.models.note import Note
from app.repositories.postgres.note import PostgresNotesRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.session import PostgresTherapySessionRepository
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

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

    schema = f"practice_test_dashboard_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def session(engine: Engine, tenant_schema: str) -> Iterator[Session]:
    sess = Session(bind=engine)
    set_tenant_schema(sess, tenant_schema)
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
        _current_tenant_schema.set(None)


def _arm(session: Session, user_id: str) -> None:
    """Arm the RLS GUC for the statements that follow, as the app does per
    request — commit first so the next statement's ``after_begin`` listener
    re-applies the newly-armed user."""
    session.commit()
    arm_current_user_id(session, user_id)


def _user() -> str:
    return str(uuid.uuid4())


def _seed_patient(session: Session, user_id: str) -> str:
    """Create a patient owned by ``user_id`` (also inserts the primary grant)."""
    patient_id = str(uuid.uuid4())
    now = datetime(2026, 1, 1, tzinfo=UTC)
    PostgresPatientRepository(session).create(
        Patient(
            id=patient_id,
            first_name="Pat",
            last_name="Ient",
            created_at=now,
            updated_at=now,
        ),
        user_id,
    )
    return patient_id


def _seed_session(  # noqa: PLR0913 — seeding helper with independent date/number knobs
    session: Session,
    *,
    user_id: str,
    patient_id: str,
    n: int,
    status: SessionStatus,
    day: int = 1,
) -> str:
    session_id = str(uuid.uuid4())
    PostgresTherapySessionRepository(session).create(
        TherapySession(
            id=session_id,
            user_id=user_id,
            patient_id=patient_id,
            session_date=datetime(2026, 6, day, 10, n % 60, tzinfo=UTC),
            session_number=n,
            status=status,
            transcript=Transcript(format="txt", content="t"),
            created_at=datetime(2026, 6, day, tzinfo=UTC),
        )
    )
    return session_id


def _seed_note(
    session: Session,
    *,
    user_id: str,
    patient_id: str,
    session_id: str | None,
    finalized_at: datetime | None,
) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    PostgresNotesRepository(session).add(
        Note(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            session_id=session_id,
            note_type="soap",
            content={"subjective": "S"},
            finalized_at=finalized_at,
            created_at=now,
            updated_at=now,
        ),
        user_id,
    )


def test_count_by_status_groups_over_full_accessible_set(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    plan = (
        [SessionStatus.PENDING_REVIEW] * 3
        + [SessionStatus.QUEUED] * 2
        + [SessionStatus.PROCESSING]
        + [SessionStatus.FINALIZED]
    )
    for n, status in enumerate(plan, 1):
        _seed_session(session, user_id=user_id, patient_id=patient_id, n=n, status=status)
    session.commit()

    _arm(session, user_id)
    counts = PostgresTherapySessionRepository(session).count_by_status(user_id)
    assert counts.get(SessionStatus.PENDING_REVIEW) == 3
    assert counts.get(SessionStatus.QUEUED) == 2
    assert counts.get(SessionStatus.PROCESSING) == 1
    assert counts.get(SessionStatus.FINALIZED) == 1


def test_list_recent_by_status_orders_desc_and_limits(session: Session) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    # Seven pending_review sessions on ascending dates, inserted out of order.
    for n, day in [(1, 5), (2, 1), (3, 7), (4, 3), (5, 2), (6, 6), (7, 4)]:
        _seed_session(
            session,
            user_id=user_id,
            patient_id=patient_id,
            n=n,
            status=SessionStatus.PENDING_REVIEW,
            day=day,
        )
    # A non-pending session must never appear in the pending_review slice.
    _seed_session(
        session, user_id=user_id, patient_id=patient_id, n=8, status=SessionStatus.FINALIZED, day=9
    )
    session.commit()

    _arm(session, user_id)
    rows = PostgresTherapySessionRepository(session).list_recent_by_status(
        user_id, SessionStatus.PENDING_REVIEW, limit=5
    )
    assert len(rows) == 5
    assert all(r.status == SessionStatus.PENDING_REVIEW for r in rows)
    # Newest session_date first (days 7,6,5,4,3 — the top 5 of 1..7).
    assert [r.session_date.day for r in rows] == [7, 6, 5, 4, 3]


def test_count_unfinalized_counts_only_session_attached_unsigned_notes(
    session: Session,
) -> None:
    user_id = _user()
    _arm(session, user_id)
    patient_id = _seed_patient(session, user_id)
    s1 = _seed_session(
        session, user_id=user_id, patient_id=patient_id, n=1, status=SessionStatus.PENDING_REVIEW
    )
    s2 = _seed_session(
        session, user_id=user_id, patient_id=patient_id, n=2, status=SessionStatus.PENDING_REVIEW
    )
    s3 = _seed_session(
        session, user_id=user_id, patient_id=patient_id, n=3, status=SessionStatus.FINALIZED
    )
    # Counts: two session-attached notes with no finalized_at.
    _seed_note(session, user_id=user_id, patient_id=patient_id, session_id=s1, finalized_at=None)
    _seed_note(session, user_id=user_id, patient_id=patient_id, session_id=s2, finalized_at=None)
    # Skipped: finalized (signed) note.
    _seed_note(
        session,
        user_id=user_id,
        patient_id=patient_id,
        session_id=s3,
        finalized_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    # Skipped: standalone note with no session_id.
    _seed_note(session, user_id=user_id, patient_id=patient_id, session_id=None, finalized_at=None)
    session.commit()

    _arm(session, user_id)
    assert PostgresNotesRepository(session).count_unfinalized(user_id) == 2


def test_queries_isolate_across_grants(session: Session) -> None:
    """User A's dashboard reads never see user B's patient's rows — proving the
    grant filter and the RLS policy together."""
    user_a, user_b = _user(), _user()

    _arm(session, user_b)
    patient_b = _seed_patient(session, user_b)
    sb = _seed_session(
        session, user_id=user_b, patient_id=patient_b, n=1, status=SessionStatus.PENDING_REVIEW
    )
    _seed_note(session, user_id=user_b, patient_id=patient_b, session_id=sb, finalized_at=None)

    _arm(session, user_a)
    patient_a = _seed_patient(session, user_a)
    _seed_session(
        session, user_id=user_a, patient_id=patient_a, n=1, status=SessionStatus.PENDING_REVIEW
    )
    sa2 = _seed_session(
        session, user_id=user_a, patient_id=patient_a, n=2, status=SessionStatus.QUEUED
    )
    _seed_note(session, user_id=user_a, patient_id=patient_a, session_id=sa2, finalized_at=None)
    session.commit()

    _arm(session, user_a)
    session_repo = PostgresTherapySessionRepository(session)
    notes_repo = PostgresNotesRepository(session)

    counts = session_repo.count_by_status(user_a)
    # Only A's own rows: 1 pending_review + 1 queued, B's pending_review excluded.
    assert counts.get(SessionStatus.PENDING_REVIEW) == 1
    assert counts.get(SessionStatus.QUEUED) == 1

    recent = session_repo.list_recent_by_status(user_a, SessionStatus.PENDING_REVIEW, limit=5)
    assert {r.patient_id for r in recent} == {patient_a}

    # A's one unsigned note; B's is not counted.
    assert notes_repo.count_unfinalized(user_a) == 1
