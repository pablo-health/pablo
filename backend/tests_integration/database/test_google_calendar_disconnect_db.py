# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres proof of what disconnecting Google Calendar removes.

What Pablo read from the calendar goes: the events it followed or asked
about, and the answers it remembered for them. Pablo's own records stay —
an appointment booked from a followed event, the session held for it and
that session's note — with only their pointers into Google cleared. A
calendar feed is a different connection and keeps everything, and so does
every other clinician in the practice.

The unit suite's repositories are in memory, so only this can show the bulk
statements touch exactly those rows under the real schema and its RLS.

Run: ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from app.calendar_providers.disconnect import Forgotten, forget_google_calendar
from app.db import _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.models.patient import Patient
from app.repositories.external_calendar_event import ANSWER_OPEN, ExternalCalendarEvent
from app.repositories.patient_source_mapping import PatientSourceMapping
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.external_calendar_event import (
    PostgresExternalCalendarEventRepository,
)
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.patient_source_mapping import (
    PostgresPatientSourceMappingRepository,
)
from app.scheduling_engine.models.appointment import Appointment
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

GOOGLE = "google_calendar"
FEED = "ical:sessions_health"


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

    schema = f"practice_test_gcal_off_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture
def sessions(engine: Engine, tenant_schema: str) -> Iterator[list[Session]]:
    opened: list[Session] = []
    yield opened
    for sess in opened:
        sess.rollback()
        sess.close()
    _current_tenant_schema.set(None)


def _as(engine: Engine, schema: str, user_id: str, opened: list[Session]) -> Session:
    sess = Session(bind=engine)
    set_tenant_schema(sess, schema)
    arm_current_user_id(sess, user_id)
    opened.append(sess)
    return sess


class _Calendar:
    """One clinician's calendar history, seeded through the real repositories."""

    def __init__(self, sess: Session, user_id: str) -> None:
        self.sess = sess
        self.user_id = user_id
        self.events = PostgresExternalCalendarEventRepository(sess)
        self.mappings = PostgresPatientSourceMappingRepository(sess)
        self.appointments = PostgresAppointmentRepository(sess)
        self._booked = 0
        now = datetime.now(UTC)
        self.patient = PostgresPatientRepository(sess).create(
            Patient(
                id=str(uuid.uuid4()),
                first_name="Followed",
                last_name="Client",
                created_at=now,
                updated_at=now,
            ),
            user_id,
        )

    def event(self, source: str, *, answer: str = ANSWER_OPEN) -> ExternalCalendarEvent:
        start = datetime(2099, 1, 5, 15, tzinfo=UTC)
        row = ExternalCalendarEvent(
            id=str(uuid.uuid4()),
            user_id=self.user_id,
            source=source,
            source_event_id=f"evt-{uuid.uuid4().hex[:8]}",
            source_series_id="series-1",
            start_at=start,
            end_at=start + timedelta(minutes=50),
            title="Followed Client",
            answer=answer,
            patient_id=self.patient.id if answer == "client" else None,
        )
        self.events.save(row)
        return row

    def remember(self, source: str) -> None:
        self.mappings.save(
            PatientSourceMapping(
                user_id=self.user_id,
                source=source,
                source_identifier=f"series:{uuid.uuid4().hex[:8]}",
                patient_id=self.patient.id,
            )
        )

    def appointment(self, **links: str) -> Appointment:
        """An appointment, carrying the given google_* / outside_* fields.

        Each on its own day: a clinician can hold one active booking per start.
        """
        self._booked += 1
        start = datetime(2099, 1, 5, 15, tzinfo=UTC) + timedelta(days=self._booked)
        appointment = Appointment(
            id=str(uuid.uuid4()),
            user_id=self.user_id,
            patient_id=self.patient.id,
            title="Session",
            start_at=start,
            end_at=start + timedelta(minutes=50),
            duration_minutes=50,
            status="confirmed",
            session_type="individual",
            created_at=start,
            updated_at=start,
        )
        for field, value in links.items():
            setattr(appointment, field, value)
        return self.appointments.create(appointment)

    def hold_session_with_note(self, appointment: Appointment) -> tuple[str, str]:
        """A session held for the appointment, and the SOAP note written for it."""
        session_id, note_id = str(uuid.uuid4()), str(uuid.uuid4())
        now = datetime.now(UTC)
        self.sess.execute(
            text(
                "INSERT INTO therapy_sessions (id, user_id, patient_id, session_date,"
                " session_number, status, transcript, created_at, updated_at,"
                " scheduled_at, duration_minutes, session_type, source)"
                " VALUES (CAST(:id AS uuid), CAST(:u AS uuid), CAST(:p AS uuid), :now,"
                " 1, 'pending_review', CAST(:tr AS jsonb), :now, :now,"
                " :now, 50, 'individual', 'companion')"
            ),
            {
                "id": session_id,
                "u": self.user_id,
                "p": self.patient.id,
                "tr": json.dumps({"format": "txt", "content": "Transcript."}),
                "now": now,
            },
        )
        self.sess.execute(
            text(
                "INSERT INTO notes (id, patient_id, session_id, note_type, content,"
                " created_at, updated_at, author_user_id)"
                " VALUES (CAST(:id AS uuid), CAST(:p AS uuid), CAST(:s AS uuid), 'soap',"
                " CAST(:c AS jsonb), :now, :now, CAST(:u AS uuid))"
            ),
            {
                "id": note_id,
                "p": self.patient.id,
                "s": session_id,
                "c": json.dumps({"subjective": "Seen.", "objective": "", "assessment": ""}),
                "now": now,
                "u": self.user_id,
            },
        )
        self.sess.execute(
            text(
                "UPDATE appointments SET session_id = CAST(:s AS uuid) WHERE id = CAST(:a AS uuid)"
            ),
            {"s": session_id, "a": appointment.id},
        )
        self.sess.flush()
        return session_id, note_id

    def reread(self, appointment: Appointment) -> Appointment:
        """The appointment as stored now; it must still be there."""
        found = self.appointments.get(appointment.id, self.user_id)
        assert found is not None, "an appointment was deleted"
        return found

    def forget(self) -> Forgotten:
        return forget_google_calendar(
            self.user_id,
            events=self.events,
            mappings=self.mappings,
            appointments=self.appointments,
        )

    def count(self, sql: str) -> int:
        return int(self.sess.execute(text(sql), {"u": self.user_id}).scalar_one())


def test_disconnect_removes_what_was_read_and_keeps_pablos_records(
    engine: Engine, tenant_schema: str, sessions: list[Session]
) -> None:
    user_id = str(uuid.uuid4())
    cal = _Calendar(_as(engine, tenant_schema, user_id, sessions), user_id)

    cal.event(GOOGLE)
    answered = cal.event(GOOGLE, answer="client")
    feed_event = cal.event(FEED)
    cal.remember(GOOGLE)
    cal.remember(GOOGLE)
    cal.remember(FEED)
    pushed = cal.appointment(
        google_event_id="pushed-evt",
        google_calendar_id="made@group.calendar.google.com",
        google_sync_status="synced",
    )
    followed = cal.appointment(
        outside_source=GOOGLE,
        outside_event_id=answered.source_event_id,
        outside_calendar_id="primary",
    )
    from_feed = cal.appointment(outside_source=FEED, outside_event_id=feed_event.source_event_id)
    # Pushed to Google once, and following a feed since: only the Google half goes.
    pushed_and_from_feed = cal.appointment(
        google_event_id="older-push",
        google_sync_status="synced",
        outside_source=FEED,
        outside_event_id="feed-event-kept",
    )
    untouched = cal.appointment()
    session_id, note_id = cal.hold_session_with_note(followed)

    forgotten = cal.forget()

    assert forgotten == Forgotten(
        calendar_events_deleted=2, remembered_answers_deleted=2, appointments_unlinked=3
    )
    assert [e.id for e in cal.events.list_by_source(user_id, GOOGLE)] == []
    assert cal.mappings.list_by_source(user_id, GOOGLE) == []
    # A followed feed is its own connection.
    assert [e.id for e in cal.events.list_by_source(user_id, FEED)] == [feed_event.id]
    assert len(cal.mappings.list_by_source(user_id, FEED)) == 1

    # Every appointment stays; only its pointers into Google go.
    still_pushed = cal.reread(pushed)
    assert (
        still_pushed.google_event_id,
        still_pushed.google_calendar_id,
        still_pushed.google_sync_status,
    ) == (None, None, None)
    still_followed = cal.reread(followed)
    assert (
        still_followed.outside_source,
        still_followed.outside_event_id,
        still_followed.outside_calendar_id,
    ) == (None, None, None)
    assert still_followed.patient_id == cal.patient.id
    still_from_feed = cal.reread(from_feed)
    assert (still_from_feed.outside_source, still_from_feed.outside_event_id) == (
        FEED,
        feed_event.source_event_id,
    )
    cal.reread(untouched)
    both = cal.reread(pushed_and_from_feed)
    assert (both.google_event_id, both.google_sync_status) == (None, None)
    assert (both.outside_source, both.outside_event_id) == (FEED, "feed-event-kept")
    assert cal.count("SELECT count(*) FROM appointments WHERE user_id = :u") == 5

    # The session held for the followed appointment, and its note, are untouched.
    assert still_followed.session_id == session_id
    assert cal.count("SELECT count(*) FROM therapy_sessions WHERE user_id = :u") == 1
    note = cal.sess.execute(
        text("SELECT session_id, deleted_at FROM notes WHERE id = CAST(:n AS uuid)"),
        {"n": note_id},
    ).one()
    assert (str(note.session_id), note.deleted_at) == (session_id, None)


def test_another_clinicians_google_calendar_is_untouched(
    engine: Engine, tenant_schema: str, sessions: list[Session]
) -> None:
    leaving, staying = str(uuid.uuid4()), str(uuid.uuid4())
    theirs = _Calendar(_as(engine, tenant_schema, staying, sessions), staying)
    their_event = theirs.event(GOOGLE)
    theirs.remember(GOOGLE)
    their_push = theirs.appointment(google_event_id="their-evt", google_sync_status="synced")
    theirs.sess.commit()

    mine = _Calendar(_as(engine, tenant_schema, leaving, sessions), leaving)
    mine.event(GOOGLE)
    mine.forget()
    mine.sess.commit()

    reread = _as(engine, tenant_schema, staying, sessions)
    events = PostgresExternalCalendarEventRepository(reread)
    assert [e.id for e in events.list_by_source(staying, GOOGLE)] == [their_event.id]
    remembered = PostgresPatientSourceMappingRepository(reread).list_by_source(staying, GOOGLE)
    assert len(remembered) == 1
    kept = PostgresAppointmentRepository(reread).get(their_push.id, staying)
    assert kept is not None
    assert kept.google_event_id == "their-evt"


def test_the_purge_is_scoped_by_clinician_even_where_rls_is_not(
    engine: Engine, tenant_schema: str, sessions: list[Session]
) -> None:
    """Each delete names its clinician itself, not only through row security.

    Under the application role RLS already hides a colleague's rows, so the
    test above cannot tell a missing ``user_id`` filter from a present one.
    A connection that bypasses RLS — the container's superuser, as a job or
    an admin path might run — can.
    """
    from tests_integration.conftest import _PgState  # noqa: PLC0415

    if _PgState.container is None:
        pytest.skip("needs the testcontainer's superuser; an external database has none here")

    leaving, staying = str(uuid.uuid4()), str(uuid.uuid4())
    theirs = _Calendar(_as(engine, tenant_schema, staying, sessions), staying)
    theirs.event(GOOGLE)
    theirs.remember(GOOGLE)
    their_push = theirs.appointment(google_event_id="their-evt", google_sync_status="synced")
    their_follow = theirs.appointment(outside_source=GOOGLE, outside_event_id="their-followed")
    theirs.sess.commit()

    superuser = create_engine(_PgState.container.get_connection_url())
    try:
        with Session(bind=superuser) as sess:
            set_tenant_schema(sess, tenant_schema)
            forget_google_calendar(
                leaving,
                events=PostgresExternalCalendarEventRepository(sess),
                mappings=PostgresPatientSourceMappingRepository(sess),
                appointments=PostgresAppointmentRepository(sess),
            )
            sess.commit()
    finally:
        superuser.dispose()
        _current_tenant_schema.set(None)

    reread = _as(engine, tenant_schema, staying, sessions)
    assert len(PostgresExternalCalendarEventRepository(reread).list_by_source(staying, GOOGLE)) == 1
    remembered = PostgresPatientSourceMappingRepository(reread).list_by_source(staying, GOOGLE)
    assert len(remembered) == 1
    appointments = PostgresAppointmentRepository(reread)
    kept_push = appointments.get(their_push.id, staying)
    kept_follow = appointments.get(their_follow.id, staying)
    assert kept_push is not None
    assert kept_follow is not None
    assert kept_push.google_event_id == "their-evt"
    assert kept_follow.outside_event_id == "their-followed"
