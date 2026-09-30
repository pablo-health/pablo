# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Real-Postgres tests for sessions followed from another service's calendar.

Two promises the unit suite can only assume, because its repositories are
in memory:

* An open row — an event nobody has said who it is, so no patient yet — is
  its own clinician's and invisible to every other clinician. It carries
  ``user_id`` and a NULL ``patient_id``, the shape a patient-access policy
  would hide from its owner and no policy at all would show to everyone.
* A calendar feed event whose client can't be matched no longer becomes an
  appointment. It used to be written with ``patient_id=''``, which Postgres
  refuses (``appointments.patient_id`` is a UUID and required), so the whole
  feed failed to sync.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from app.db import _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.models.patient import Patient
from app.repositories.external_calendar_event import ExternalCalendarEvent
from app.repositories.ical_sync_config import ICalSyncConfig
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.external_calendar_event import (
    PostgresExternalCalendarEventRepository,
)
from app.repositories.postgres.patient import PostgresPatientRepository
from app.repositories.postgres.patient_source_mapping import (
    PostgresPatientSourceMappingRepository,
)
from app.services.ical_sync_service import ICalSyncService
from app.services.token_encryption import encrypt_tokens
from app.settings import get_settings
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

# A feed whose titles name nobody on the caseload.
_FEED = "test_feed"
_ICAL = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:feed-event-1
DTSTART:20990105T150000Z
DTEND:20990105T155000Z
SUMMARY:Client 7
END:VEVENT
BEGIN:VEVENT
UID:feed-event-2
DTSTART:20990112T150000Z
DTEND:20990112T155000Z
SUMMARY:Client 7
END:VEVENT
END:VCALENDAR"""


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

    schema = f"practice_test_outside_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _session(engine: Engine, schema: str, user_id: str) -> Session:
    sess = Session(bind=engine)
    set_tenant_schema(sess, schema)
    arm_current_user_id(sess, user_id)
    return sess


@pytest.fixture
def as_clinician(engine: Engine, tenant_schema: str) -> Iterator[list[Session]]:
    """Opened sessions, each armed as one clinician; all closed afterwards."""
    opened: list[Session] = []
    yield opened
    for sess in opened:
        sess.rollback()
        sess.close()
    _current_tenant_schema.set(None)


def _open_row(user_id: str) -> ExternalCalendarEvent:
    start = datetime(2099, 1, 5, 15, tzinfo=UTC)
    return ExternalCalendarEvent(
        id=str(uuid.uuid4()),
        user_id=user_id,
        source="google_calendar",
        source_event_id=f"evt-{uuid.uuid4().hex[:8]}",
        source_series_id="series-1",
        start_at=start,
        end_at=start + timedelta(minutes=50),
        title="Weekly 1:1",
    )


def test_an_open_row_is_its_clinicians_and_nobody_elses(
    engine: Engine, tenant_schema: str, as_clinician: list[Session]
) -> None:
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    row = _open_row(owner)
    with Session(bind=engine) as writer:
        set_tenant_schema(writer, tenant_schema)
        arm_current_user_id(writer, owner)
        PostgresExternalCalendarEventRepository(writer).save(row)
        writer.commit()

    mine = _session(engine, tenant_schema, owner)
    theirs = _session(engine, tenant_schema, other)
    as_clinician.extend([mine, theirs])

    assert [r.id for r in PostgresExternalCalendarEventRepository(mine).list_open(owner)] == [
        row.id
    ]
    # Even asked for by the owner's id, the other clinician's session sees nothing.
    assert PostgresExternalCalendarEventRepository(theirs).list_open(owner) == []
    assert PostgresExternalCalendarEventRepository(theirs).get_by_id(owner, row.id) is None
    count = theirs.execute(text("SELECT count(*) FROM external_calendar_events")).scalar_one()
    assert count == 0


def test_the_table_is_row_scoped_by_its_clinician(engine: Engine, tenant_schema: str) -> None:
    with engine.connect() as conn:
        forced = conn.execute(
            text(
                "SELECT c.relrowsecurity AND c.relforcerowsecurity FROM pg_class c"
                " JOIN pg_namespace n ON n.oid = c.relnamespace"
                " WHERE n.nspname = :s AND c.relname = 'external_calendar_events'"
            ),
            {"s": tenant_schema},
        ).scalar()
        policies = {
            r[0]
            for r in conn.execute(
                text(
                    "SELECT policyname FROM pg_policies"
                    " WHERE schemaname = :s AND tablename = 'external_calendar_events'"
                ),
                {"s": tenant_schema},
            )
        }
    assert forced is True
    assert "rls_user_isolation" in policies


def _feed_service(sess: Session, user_id: str) -> ICalSyncService:
    config = ICalSyncConfig(
        user_id=user_id,
        ehr_system=_FEED,
        encrypted_feed_url=encrypt_tokens({"feed_url": "https://feed.test/cal"}),
        connected_at=datetime.now(UTC),
    )
    configs = MagicMock()
    configs.list_by_user.return_value = [config]
    return ICalSyncService(
        config_repo=configs,
        appointment_repo=PostgresAppointmentRepository(sess),
        patient_repo=PostgresPatientRepository(sess),
        mapping_repo=PostgresPatientSourceMappingRepository(sess),
        external_events=PostgresExternalCalendarEventRepository(sess),
    )


def test_an_unmatched_feed_event_is_held_and_the_feed_syncs(
    engine: Engine, tenant_schema: str, as_clinician: list[Session]
) -> None:
    user_id = str(uuid.uuid4())
    sess = _session(engine, tenant_schema, user_id)
    as_clinician.append(sess)
    feed = _feed_service(sess, user_id)

    with patch.object(ICalSyncService, "_fetch_feed", return_value=_ICAL):
        [result] = feed.sync(user_id)

    assert result.errors == []
    assert result.created == 0
    appointments = sess.execute(
        text("SELECT count(*) FROM appointments WHERE user_id = :u"), {"u": user_id}
    ).scalar_one()
    assert appointments == 0
    held = PostgresExternalCalendarEventRepository(sess).list_open(user_id)
    assert sorted(r.source_event_id for r in held) == ["feed-event-1", "feed-event-2"]
    assert {r.source for r in held} == {f"ical:{_FEED}"}


def test_resolving_the_client_books_the_held_events(
    engine: Engine, tenant_schema: str, as_clinician: list[Session]
) -> None:
    user_id = str(uuid.uuid4())
    sess = _session(engine, tenant_schema, user_id)
    as_clinician.append(sess)
    feed = _feed_service(sess, user_id)
    now = datetime.now(UTC)
    patient = PostgresPatientRepository(sess).create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Seven",
            last_name="Client",
            created_at=now,
            updated_at=now,
        ),
        user_id,
    )
    with patch.object(ICalSyncService, "_fetch_feed", return_value=_ICAL):
        feed.sync(user_id)

        feed.resolve_client(user_id, _FEED, "Client 7", patient.id)
        [again] = feed.sync(user_id)

    booked = PostgresAppointmentRepository(sess).list_by_ical_source(user_id, _FEED)
    assert sorted(a.outside_event_id or "" for a in booked) == ["feed-event-1", "feed-event-2"]
    assert {a.patient_id for a in booked} == {patient.id}
    assert (again.errors, again.created, again.unchanged) == ([], 0, 2)
    assert PostgresExternalCalendarEventRepository(sess).list_open(user_id) == []
