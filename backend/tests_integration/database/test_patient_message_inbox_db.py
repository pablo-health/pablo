# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice's inbox against real Postgres.

Runs the real ``PostgresPatientMessageRepository`` on a schema built by
``create_practice_schema``, connected as the ``NOBYPASSRLS`` role with only a
clinician armed — so the grant predicate in the queries and the row policy
underneath them are both in force, as in production.

Proves what only the SQL can: a patient the clinician has no grant on is
absent from both views and the count; a deleted patient's threads are left
out; the grouped view orders unread first then newest; and the ungrouped
view lists each client message on its own row, newest first, with the
practice's replies left out.

The route wiring and audit rows are covered in
``tests/test_patient_message_inbox_api.py``.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.repositories.postgres.patient_message import PostgresPatientMessageRepository
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Connection, Engine

_DB_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _DB_URL or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres "
        "or run via make test-integration."
    ),
)

_CLINICIAN = "5b1c9e24-7a30-4f8d-9e61-2c4b8d0a7f13"
_OTHER_CLINICIAN = "8e2d4a61-3c9f-4b07-a5d2-6f1e0b9c3a48"
_T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


@dataclass(frozen=True)
class _Practice:
    """A provisioned practice schema and the engine that reaches it."""

    engine: Engine
    schema: str

    def connect(self, *, user: str | None = None, patient: str | None = None) -> Connection:
        """A connection with exactly one principal armed (or none)."""
        conn = self.engine.connect()
        conn.execute(text(f"SET search_path = {self.schema}, platform, public"))
        conn.execute(text("RESET app.current_user_id"))
        conn.execute(text("RESET app.current_patient_id"))
        if user is not None:
            conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": user})
        if patient is not None:
            conn.execute(
                text("SELECT set_config('app.current_patient_id', :p, false)"), {"p": patient}
            )
        return conn

    def run(self, sql: str, params: dict[str, object], **principal: str) -> None:
        conn = self.connect(**principal)
        try:
            conn.execute(text(sql), params)
            conn.commit()
        finally:
            conn.close()


@pytest.fixture(scope="module")
def practice() -> Iterator[_Practice]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    engine = create_engine(_DB_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    schema = f"practice_test_inbox_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield _Practice(engine, schema)
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()
    engine.dispose()


def _patient(practice: _Practice, first: str, last: str, *, clinician: str) -> str:
    """A patient created by *clinician*, who therefore holds the grant."""
    patient_id = str(uuid.uuid4())
    practice.run(
        "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
        "last_name_lower, status, session_count, created_at, updated_at) "
        "VALUES (CAST(:pid AS uuid), :first, :last, lower(:first), lower(:last), "
        "'active', 0, now(), now())",
        {"pid": patient_id, "first": first, "last": last},
        user=clinician,
    )
    practice.run(
        "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
        "VALUES (CAST(:pid AS uuid), :u, :u)",
        {"pid": patient_id, "u": clinician},
        user=clinician,
    )
    return patient_id


def _message(
    practice: _Practice,
    where: tuple[str, str],
    *,
    at: datetime,
    body: str,
    sender: str = "patient",
) -> None:
    """A message in thread ``where = (patient_id, thread_id)``, written by
    whichever principal the sender is."""
    patient_id, thread_id = where
    principal = {"patient": patient_id} if sender == "patient" else {"user": _CLINICIAN}
    practice.run(
        "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, created_at) "
        "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:pid AS uuid), :s, :b, :at)",
        {
            "id": str(uuid.uuid4()),
            "t": thread_id,
            "pid": patient_id,
            "s": sender,
            "b": body,
            "at": at,
        },
        **principal,
    )
    practice.run(
        "UPDATE patient_message_threads SET last_message_at = :at "
        "WHERE id = CAST(:t AS uuid) AND last_message_at < :at",
        {"t": thread_id, "at": at},
        **principal,
    )


def _thread(practice: _Practice, patient_id: str, *, at: datetime) -> str:
    """A thread the patient opened, with their first message."""
    thread_id = str(uuid.uuid4())
    practice.run(
        "INSERT INTO patient_message_threads "
        "(id, patient_id, subject, status, created_at, last_message_at) "
        "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'seed', 'open', :at, :at)",
        {"id": thread_id, "pid": patient_id, "at": at},
        patient=patient_id,
    )
    _message(practice, (patient_id, thread_id), at=at, body=f"opened {thread_id[:4]}")
    return thread_id


@pytest.fixture(scope="module")
def seeded(practice: _Practice) -> dict[str, str]:
    """Ada and Grace are the clinician's; Linus is somebody else's; Hedy is
    the clinician's but deleted."""
    ada = _patient(practice, "Ada", "Lovelace", clinician=_CLINICIAN)
    grace = _patient(practice, "Grace", "Hopper", clinician=_CLINICIAN)
    linus = _patient(practice, "Linus", "Other", clinician=_OTHER_CLINICIAN)
    hedy = _patient(practice, "Hedy", "Lamarr", clinician=_CLINICIAN)

    ada_thread = _thread(practice, ada, at=_T0)
    _message(
        practice,
        (ada, ada_thread),
        at=_T0 + timedelta(minutes=5),
        body="reply",
        sender="clinician",
    )
    _message(practice, (ada, ada_thread), at=_T0 + timedelta(minutes=10), body="ada again")
    grace_thread = _thread(practice, grace, at=_T0 + timedelta(minutes=7))
    _thread(practice, linus, at=_T0 + timedelta(minutes=20))
    _thread(practice, hedy, at=_T0 + timedelta(minutes=30))

    # Grace's thread has been read; Ada's has not. Hedy's chart is deleted.
    practice.run(
        "UPDATE patient_message_threads SET clinician_last_read_at = :at "
        "WHERE id = CAST(:t AS uuid)",
        {"t": grace_thread, "at": _T0 + timedelta(hours=1)},
        user=_CLINICIAN,
    )
    practice.run(
        "UPDATE patients SET deleted_at = now() WHERE id = CAST(:p AS uuid)",
        {"p": hedy},
        user=_CLINICIAN,
    )
    return {"ada_thread": ada_thread, "grace_thread": grace_thread}


@contextmanager
def _inbox_as(practice: _Practice, user: str) -> Iterator[PostgresPatientMessageRepository]:
    """The repository on a connection armed as *user*, closed afterwards.

    The connection is closed, not just the session: its transaction began
    with the ``SET search_path`` and the session only joins it, so closing
    the session alone would leave the reads' locks held — and the module
    teardown's ``DROP SCHEMA`` waiting on them for good.
    """
    conn = practice.connect(user=user)
    session = Session(bind=conn)
    try:
        yield PostgresPatientMessageRepository(session)
    finally:
        session.close()
        conn.close()


def test_conversations_are_the_clinicians_own_unread_first(
    practice: _Practice, seeded: dict[str, str]
) -> None:
    with _inbox_as(practice, _CLINICIAN) as repo:
        rows = repo.list_inbox_threads(_CLINICIAN)

    assert [row.thread.id for row in rows] == [seeded["ada_thread"], seeded["grace_thread"]]
    ada, grace = rows
    assert ada.patient_name == "Ada Lovelace"
    assert ada.unread_count == 2
    assert grace.unread_count == 0


def test_every_client_message_is_its_own_row_newest_first(
    practice: _Practice, seeded: dict[str, str]
) -> None:
    with _inbox_as(practice, _CLINICIAN) as repo:
        rows = repo.list_inbox_messages(_CLINICIAN)
        unread = repo.list_inbox_messages(_CLINICIAN, unread_only=True)

    assert [(row.patient_name, row.message.body) for row in rows] == [
        ("Ada Lovelace", "ada again"),
        ("Grace Hopper", f"opened {seeded['grace_thread'][:4]}"),
        ("Ada Lovelace", f"opened {seeded['ada_thread'][:4]}"),
    ]
    assert [row.unread for row in rows] == [True, False, True]
    assert [row.message.body for row in unread] == [
        "ada again",
        f"opened {seeded['ada_thread'][:4]}",
    ]


@pytest.mark.usefixtures("seeded")
def test_the_badge_counts_threads_with_something_unread(practice: _Practice) -> None:
    with _inbox_as(practice, _CLINICIAN) as repo:
        assert repo.count_unread_threads(_CLINICIAN) == 1


@pytest.mark.usefixtures("seeded")
def test_another_clinicians_patient_is_theirs_alone(practice: _Practice) -> None:
    """The control for the absences above: the other clinician sees their
    own patient, so Linus missing from the first clinician's inbox is the
    grant working, not an empty table."""
    with _inbox_as(practice, _OTHER_CLINICIAN) as repo:
        assert [row.patient_name for row in repo.list_inbox_threads(_OTHER_CLINICIAN)] == [
            "Linus Other"
        ]
        assert repo.count_unread_threads(_OTHER_CLINICIAN) == 1
