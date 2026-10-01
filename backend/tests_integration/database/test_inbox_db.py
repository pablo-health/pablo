# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The Inbox against real Postgres.

Runs the real repositories and sources on a schema built by
``create_practice_schema``, connected as the ``NOBYPASSRLS`` role with one
clinician armed, so the grant predicates in the queries and the row policies
underneath them are both in force.

Proves what only the SQL can:

* ``inbox_item_states`` is each clinician's own. The table carries
  ``user_id``, so ``enable_rls_on_schema`` gives it the direct-ownership
  policy: one clinician can neither read nor write another's rows, even
  when they name the same item.
* A state supersedes the one before it rather than editing it, and one live
  row per clinician per item is the database's rule, not only the code's.
* The new practice-wide queries — client messages still waiting, forms
  waiting for review, calendar changes to settle — return what the sources
  need, scoped by the grant, with handled messages left out in SQL.
* Every built-in source, assembled by the service over these repositories,
  lists its open item and drops it once it is handled.

The route wiring, the reply behaviour and the audit rows are covered in
``tests/test_inbox_api.py``.
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
from app.inbox.registry import InboxContext, InboxRegistry
from app.inbox.service import InboxService
from app.inbox.sources import (
    CalendarChangeSource,
    IntakeReviewSource,
    NoteToSignSource,
    PortalMessageSource,
    RefillSource,
)
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.inbox_item_state import PostgresInboxItemStateRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.patient_intake_assignment import (
    PostgresPatientIntakeAssignmentRepository,
)
from app.repositories.postgres.patient_message import PostgresPatientMessageRepository
from app.repositories.postgres.refill_request import PostgresRefillRequestRepository
from app.repositories.postgres.session import PostgresTherapySessionRepository
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
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
    schema = f"practice_test_inbox_states_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield _Practice(engine, schema)
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()
    engine.dispose()


@contextmanager
def _session_as(practice: _Practice, user: str) -> Iterator[Session]:
    """A session on a connection armed as *user*; committed, then closed.

    Both commits are needed: the connection's transaction began with the
    ``SET search_path``, and the session only joins it.

    The connection is closed, not just the session, so the reads' locks do
    not outlive the test and hold up the module teardown's ``DROP SCHEMA``.
    """
    conn = practice.connect(user=user)
    session = Session(bind=conn)
    try:
        yield session
        session.commit()
        conn.commit()
    finally:
        session.close()
        conn.close()


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


def _thread(practice: _Practice, patient_id: str, *, status: str = "open") -> str:
    thread_id = str(uuid.uuid4())
    practice.run(
        "INSERT INTO patient_message_threads "
        "(id, patient_id, subject, status, created_at, last_message_at) "
        "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), 'Tuesday', :s, now(), now())",
        {"id": thread_id, "pid": patient_id, "s": status},
        patient=patient_id,
    )
    return thread_id


def _client_message(practice: _Practice, patient_id: str, thread_id: str, at: datetime) -> str:
    message_id = str(uuid.uuid4())
    practice.run(
        "INSERT INTO patient_messages (id, thread_id, patient_id, sender, body, created_at) "
        "VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:pid AS uuid), 'patient', "
        "'hello', :at)",
        {"id": message_id, "t": thread_id, "pid": patient_id, "at": at},
        patient=patient_id,
    )
    return message_id


def _count_states(practice: _Practice, user: str) -> int:
    conn = practice.connect(user=user)
    try:
        return int(conn.execute(text("SELECT count(*) FROM inbox_item_states")).scalar() or 0)
    finally:
        conn.close()


# ── inbox_item_states: each clinician's own ──────────────────────────────


def test_one_clinician_cannot_read_or_write_anothers_states(practice: _Practice) -> None:
    now = datetime.now(UTC)
    key = ("refill", str(uuid.uuid4()))
    with _session_as(practice, _CLINICIAN) as session:
        PostgresInboxItemStateRepository(session).record(_CLINICIAN, key, "dismissed", now)

    # Control: the owner sees their row.
    with _session_as(practice, _CLINICIAN) as session:
        assert PostgresInboxItemStateRepository(session).live_states(_CLINICIAN, [key])
    assert _count_states(practice, _CLINICIAN) >= 1

    # Another clinician sees nothing, whether asked through the repository
    # or with no filter at all, and cannot read the row by naming its owner.
    assert _count_states(practice, _OTHER_CLINICIAN) == 0
    with _session_as(practice, _OTHER_CLINICIAN) as session:
        repo = PostgresInboxItemStateRepository(session)
        assert repo.live_states(_OTHER_CLINICIAN, [key]) == {}
        assert repo.live_states(_CLINICIAN, [key]) == {}
        assert repo.list_hidden(_CLINICIAN, now, kinds=None, limit=10) == []

    # Nor write one in their colleague's name.
    with pytest.raises(DBAPIError, match="row-level security"):
        practice.run(
            "INSERT INTO inbox_item_states "
            "(id, user_id, source_kind, source_id, disposition, resolved_at) "
            "VALUES (gen_random_uuid(), CAST(:u AS uuid), 'refill', 'x', 'dismissed', now())",
            {"u": _CLINICIAN},
            user=_OTHER_CLINICIAN,
        )

    # And their own state for the same item is theirs alone: it does not
    # touch the first clinician's.
    with _session_as(practice, _OTHER_CLINICIAN) as session:
        PostgresInboxItemStateRepository(session).record(_OTHER_CLINICIAN, key, "snoozed", now)
    with _session_as(practice, _CLINICIAN) as session:
        [state] = PostgresInboxItemStateRepository(session).live_states(_CLINICIAN, [key]).values()
    assert state.disposition == "dismissed"


def test_a_state_supersedes_the_one_before_it(practice: _Practice) -> None:
    now = datetime.now(UTC)
    key = ("portal_message", str(uuid.uuid4()))
    with _session_as(practice, _CLINICIAN) as session:
        repo = PostgresInboxItemStateRepository(session)
        repo.record(_CLINICIAN, key, "handled", now)
        repo.record(_CLINICIAN, key, "restored", now + timedelta(seconds=1))
        repo.record(
            _CLINICIAN,
            key,
            "snoozed",
            now + timedelta(seconds=2),
            snoozed_until=now + timedelta(days=1),
        )
        live = repo.live_states(_CLINICIAN, [key])[key]
        hidden_now = repo.list_hidden(
            _CLINICIAN, now + timedelta(seconds=3), kinds={"portal_message"}, limit=100
        )
        hidden_later = repo.list_hidden(
            _CLINICIAN, now + timedelta(days=2), kinds={"portal_message"}, limit=100
        )
        history = session.execute(
            text(
                "SELECT disposition, superseded_by IS NULL FROM inbox_item_states "
                "WHERE source_id = :id ORDER BY resolved_at"
            ),
            {"id": key[1]},
        ).all()

    assert live.disposition == "snoozed"
    assert key[1] in [s.source_id for s in hidden_now]
    assert key[1] not in [s.source_id for s in hidden_later]
    assert [tuple(row) for row in history] == [
        ("handled", False),
        ("restored", False),
        ("snoozed", True),
    ]


def test_two_live_states_for_one_item_are_refused(practice: _Practice) -> None:
    key_id = str(uuid.uuid4())
    insert = (
        "INSERT INTO inbox_item_states "
        "(id, user_id, source_kind, source_id, disposition, resolved_at) "
        "VALUES (gen_random_uuid(), CAST(:u AS uuid), 'refill', :id, 'dismissed', now())"
    )
    practice.run(insert, {"u": _CLINICIAN, "id": key_id}, user=_CLINICIAN)
    with pytest.raises(DBAPIError, match="ux_inbox_item_states_live"):
        practice.run(insert, {"u": _CLINICIAN, "id": key_id}, user=_CLINICIAN)


# ── the practice-wide queries ────────────────────────────────────────────


def test_waiting_messages_leave_out_handled_closed_and_ungranted(practice: _Practice) -> None:
    now = datetime.now(UTC)
    ada = _patient(practice, "Ada", "Lovelace", clinician=_CLINICIAN)
    linus = _patient(practice, "Linus", "Other", clinician=_OTHER_CLINICIAN)
    open_thread = _thread(practice, ada)
    closed_thread = _thread(practice, ada, status="closed")
    theirs = _thread(practice, linus)
    first = _client_message(practice, ada, open_thread, now - timedelta(hours=2))
    second = _client_message(practice, ada, open_thread, now - timedelta(hours=1))
    replied = _client_message(practice, ada, open_thread, now)
    _client_message(practice, ada, closed_thread, now)
    _client_message(practice, linus, theirs, now)

    with _session_as(practice, _CLINICIAN) as session:
        PostgresInboxItemStateRepository(session).record(
            _CLINICIAN, ("portal_message", replied), "replied", now
        )
        PostgresInboxItemStateRepository(session).record(
            _CLINICIAN,
            ("portal_message", second),
            "snoozed",
            now,
            snoozed_until=now - timedelta(minutes=1),
        )
        repo = PostgresPatientMessageRepository(session)
        waiting = repo.list_awaiting_messages(_CLINICIAN, now=now)
        for_ada = repo.list_awaiting_messages(_CLINICIAN, now=now, patient_id=ada)
        fetched = repo.get_inbox_messages(_CLINICIAN, [replied])

    ids = [row.message.id for row in waiting]
    assert second in ids  # a snooze that ran out is waiting again
    assert first in ids
    assert ids.index(second) < ids.index(first)  # newest first
    assert replied not in ids
    assert all(row.thread.status == "open" for row in waiting)
    assert all(row.thread.patient_id != linus for row in waiting)
    assert [row.message.id for row in for_ada] == [second, first]
    assert [row.message.id for row in fetched] == [replied]
    assert fetched[0].patient_name == "Ada Lovelace"

    # The other clinician's view of the same message is untouched by the first
    # clinician's states — and they have no grant on Ada anyway.
    with _session_as(practice, _OTHER_CLINICIAN) as session:
        repo = PostgresPatientMessageRepository(session)
        assert repo.get_inbox_messages(_OTHER_CLINICIAN, [first]) == []


# ── every source, over Postgres, through the service ─────────────────────


def _seed_every_kind(practice: _Practice, patient_id: str) -> dict[str, str]:
    now = datetime.now(UTC)
    ids = {
        "refill": str(uuid.uuid4()),
        "intake_review": str(uuid.uuid4()),
        "note_to_sign": str(uuid.uuid4()),
        "calendar_change": str(uuid.uuid4()),
    }
    thread = _thread(practice, patient_id)
    ids["portal_message"] = _client_message(practice, patient_id, thread, now)
    practice.run(
        "INSERT INTO refill_requests (id, patient_id, medication_text, status, created_at, "
        "updated_at) VALUES (CAST(:id AS uuid), CAST(:p AS uuid), 'Sertraline 50 mg', "
        "'requested', now(), now())",
        {"id": ids["refill"], "p": patient_id},
        user=_CLINICIAN,
    )
    template, version = str(uuid.uuid4()), str(uuid.uuid4())
    practice.run(
        "INSERT INTO intake_packet_templates (id, name, created_at) "
        "VALUES (CAST(:t AS uuid), 'New client intake', now())",
        {"t": template},
        user=_CLINICIAN,
    )
    practice.run(
        "INSERT INTO intake_packet_versions (id, template_id, version, created_at, published_at) "
        "VALUES (CAST(:v AS uuid), CAST(:t AS uuid), 1, now(), now())",
        {"v": version, "t": template},
        user=_CLINICIAN,
    )
    practice.run(
        "INSERT INTO patient_intake_assignments (id, patient_id, version_id, status, "
        "assigned_at, submitted_at, updated_at) VALUES (CAST(:id AS uuid), CAST(:p AS uuid), "
        "CAST(:v AS uuid), 'submitted', now(), now(), now())",
        {"id": ids["intake_review"], "p": patient_id, "v": version},
        user=_CLINICIAN,
    )
    practice.run(
        "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date, session_number, "
        "status, transcript, created_at) VALUES (CAST(:id AS uuid), CAST(:u AS uuid), "
        "CAST(:p AS uuid), now(), 1, 'pending_review', "
        '\'{"format": "txt", "content": "x"}\'::jsonb, now())',
        {"id": ids["note_to_sign"], "u": _CLINICIAN, "p": patient_id},
        user=_CLINICIAN,
    )
    practice.run(
        "INSERT INTO appointments (id, user_id, patient_id, title, start_at, end_at, "
        "duration_minutes, status, session_type, is_exception, reminder_24h_sent, "
        "reminder_1h_sent, created_at, updated_at, google_sync_status) VALUES "
        "(CAST(:id AS uuid), CAST(:u AS uuid), CAST(:p AS uuid), 'Session', "
        "now() + interval '2 days', now() + interval '2 days 50 minutes', 50, 'confirmed', "
        "'individual', false, false, false, now(), now(), 'external_change')",
        {"id": ids["calendar_change"], "u": _CLINICIAN, "p": patient_id},
        user=_CLINICIAN,
    )
    return ids


def _service(session: Session) -> InboxService:
    registry = InboxRegistry()
    for source in (
        PortalMessageSource(lambda: PostgresPatientMessageRepository(session)),
        RefillSource(lambda: PostgresRefillRequestRepository(session)),
        IntakeReviewSource(lambda: PostgresPatientIntakeAssignmentRepository(session)),
        NoteToSignSource(
            lambda: PostgresTherapySessionRepository(session),
            lambda: PostgresPatientRepository(session),
        ),
        CalendarChangeSource(
            lambda: PostgresAppointmentRepository(session),
            lambda: PostgresPatientRepository(session),
        ),
    ):
        registry.register_source(source)
    return InboxService(registry, PostgresInboxItemStateRepository(session))


def test_every_source_lists_its_item_and_drops_it_once_handled(practice: _Practice) -> None:
    grace = _patient(practice, "Grace", "Hopper", clinician=_CLINICIAN)
    ids = _seed_every_kind(practice, grace)

    def open_for(user: str) -> dict[str, str]:
        with _session_as(practice, user) as session:
            items = _service(session).open_items(InboxContext(user_id=user, now=datetime.now(UTC)))
        return {item.kind: item.source_id for item in items if item.patient_id == grace}

    assert open_for(_CLINICIAN) == ids
    assert open_for(_OTHER_CLINICIAN) == {}  # no grant, nothing listed

    with _session_as(practice, _CLINICIAN) as session:
        intake = _service(session).find(
            InboxContext(user_id=_CLINICIAN, now=datetime.now(UTC)),
            "intake_review",
            ids["intake_review"],
        )
    assert intake.title == "Form to review: New client intake"
    assert intake.patient_name == "Grace Hopper"

    # Handled where each one lives.
    practice.run(
        "UPDATE refill_requests SET status = 'approved' WHERE id = CAST(:id AS uuid)",
        {"id": ids["refill"]},
        user=_CLINICIAN,
    )
    practice.run(
        "UPDATE patient_intake_assignments SET status = 'accepted', accepted_at = now() "
        "WHERE id = CAST(:id AS uuid)",
        {"id": ids["intake_review"]},
        user=_CLINICIAN,
    )
    practice.run(
        "UPDATE therapy_sessions SET status = 'finalized' WHERE id = CAST(:id AS uuid)",
        {"id": ids["note_to_sign"]},
        user=_CLINICIAN,
    )
    practice.run(
        "UPDATE appointments SET google_sync_status = 'synced' WHERE id = CAST(:id AS uuid)",
        {"id": ids["calendar_change"]},
        user=_CLINICIAN,
    )
    with _session_as(practice, _CLINICIAN) as session:
        ctx = InboxContext(user_id=_CLINICIAN, now=datetime.now(UTC))
        service = _service(session)
        message = service.find(ctx, "portal_message", ids["portal_message"])
        service.record(ctx, message, "replied")

    assert open_for(_CLINICIAN) == {}
    with _session_as(practice, _CLINICIAN) as session:
        done = _service(session).list_items(
            InboxContext(user_id=_CLINICIAN, now=datetime.now(UTC)), "done"
        )
    assert (ids["portal_message"], "replied") in [(i.source_id, i.disposition) for i in done]
