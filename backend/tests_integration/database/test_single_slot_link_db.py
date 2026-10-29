# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Two visitors use one single-slot link at once; exactly one gets the time.

The guarantee lives in Postgres, so it is tested there: the claim is a
conditional UPDATE on the link row, and the second visitor's UPDATE waits on
the first one's row lock. Each visitor runs ``book_slot_link`` — the function
the booking POST calls — on its own connection and thread, and the first
holds its transaction open long enough that the second is certainly waiting
on it rather than arriving after it.

Run: ``make test-integration``.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.db import PLATFORM_SCHEMA, _current_tenant_schema, arm_current_user_id, set_tenant_schema
from app.db.platform_models import BookingLinkRow, PlatformUserRow
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.appointment_type import PostgresAppointmentTypeRepository
from app.repositories.postgres.booking_link import PostgresBookingLinkRepository
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.services.scheduling import SchedulingService
from app.scheduling_engine.services.scheduling_policy import DEFAULTS
from app.services.slot_link_booking import SlotTakenError, book_slot_link
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests_integration.database.patient_rows import seed_patient_row

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

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_slot_link_{_SUFFIX}"
_OWNER = str(uuid.uuid4())
_TYPE = AppointmentType(
    id=str(uuid.uuid4()),
    user_id=_OWNER,
    name="Therapy session",
    duration_minutes=50,
    audience="both",
    self_bookable=True,
    offerable=True,
)
#: Each test books its own time, so one test's booking never collides with
#: the next test's slot.
_DAY = (datetime.now(UTC) + timedelta(days=10)).replace(hour=0, minute=0, second=0, microsecond=0)

#: How long the first visitor keeps its transaction open after claiming. Long
#: enough that the second is blocked on the row lock, not merely late.
_HOLD_SECONDS = 0.5


@pytest.fixture(scope="module", autouse=True)
def _arming_here_stays_here() -> Iterator[None]:
    """Hand back the request ContextVars as this module found them."""
    from app.db import _current_user_id  # noqa: PLC0415

    tokens = [(var, var.set(var.get())) for var in (_current_user_id, _current_tenant_schema)]
    yield
    for var, token in reversed(tokens):
        var.reset(token)


@pytest.fixture(scope="module")
def tenant(engine: Engine) -> Iterator[None]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    create_practice_schema(engine, _SCHEMA)
    with Session(bind=engine) as s:
        s.add(
            PlatformUserRow(
                id=_OWNER,
                email=f"slot-link-{_SUFFIX}@example.test",
                name="Practice Owner",
                created_at=datetime.now(UTC),
                status="approved",
                is_platform_admin=False,
                chat_quality_review_opt_in=False,
                session_notes_quality_review_opt_in=False,
            )
        )
        s.commit()
    # The type has to exist in the practice: appointments.appointment_type_id
    # is a foreign key into it, so a booking of a type that is not there is a
    # constraint violation, not a taken time.
    with _owner_session(engine) as s:
        PostgresAppointmentTypeRepository(s).create(_TYPE)
        s.commit()
    yield
    with engine.connect() as conn:
        conn.execute(
            text(f"DELETE FROM {PLATFORM_SCHEMA}.booking_links WHERE user_id::text = :u"),  # noqa: S608
            {"u": _OWNER},
        )
        conn.execute(
            text(f"DELETE FROM {PLATFORM_SCHEMA}.users WHERE id::text = :u"),  # noqa: S608
            {"u": _OWNER},
        )
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.commit()


def _owner_session(engine: Engine) -> Session:
    session = Session(bind=engine)
    set_tenant_schema(session, _SCHEMA)
    arm_current_user_id(session, _OWNER)
    return session


def _seed_link(engine: Engine, start: datetime) -> str:
    """A single-slot link for ``start``; returns its slug."""
    slug = f"slot-{uuid.uuid4().hex[:16]}"
    now = datetime.now(UTC)
    with Session(bind=engine) as s:
        s.add(
            BookingLinkRow(
                id=str(uuid.uuid4()),
                slug=slug,
                user_id=_OWNER,
                practice_id=None,
                host_name="Test Host",
                title="Earlier time",
                appointment_type_id=_TYPE.id,
                is_active=True,
                created_at=now,
                updated_at=now,
                slot_start_at=start,
                slot_end_at=start + timedelta(minutes=_TYPE.duration_minutes),
            )
        )
        s.commit()
    return slug


def _live_at(engine: Engine, start: datetime) -> int:
    with engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _OWNER})
        count = conn.execute(
            text(
                f'SELECT count(*) FROM "{_SCHEMA}".appointments '  # noqa: S608
                "WHERE start_at = :s AND status <> 'cancelled'"
            ),
            {"s": start},
        ).scalar_one()
        conn.rollback()
    return int(count)


def _visit(engine: Engine, slug: str, patient_id: str, outcomes: list[str]) -> None:
    """One visitor: claim and book, then hold the transaction a moment and commit."""
    session = _owner_session(engine)
    try:
        link = PostgresBookingLinkRepository(session).get_by_slug(slug)
        assert link is not None
        try:
            book_slot_link(
                link,
                link_repo=PostgresBookingLinkRepository(session),
                scheduling=SchedulingService(PostgresAppointmentRepository(session)),
                appointment_type=_TYPE,
                policy={**DEFAULTS, "self_book_mode": "auto"},
                patient_for_booking=lambda: patient_id,
                notes="Booked via a single-slot link.",
                now=datetime.now(UTC),
            )
        except SlotTakenError:
            session.rollback()
            outcomes.append("taken")
            return
        time.sleep(_HOLD_SECONDS)
        session.commit()
        outcomes.append("booked")
    finally:
        session.close()
        _current_tenant_schema.set(None)


def test_two_simultaneous_uses_make_exactly_one_booking(engine: Engine, tenant: None) -> None:
    start_at = _DAY.replace(hour=15)
    slug = _seed_link(engine, start_at)
    with _owner_session(engine) as s:
        patients = [seed_patient_row(s, _OWNER), seed_patient_row(s, _OWNER)]
        s.commit()

    outcomes: list[str] = []
    start = threading.Barrier(2)

    def run(patient_id: str) -> None:
        start.wait()
        _visit(engine, slug, patient_id, outcomes)

    threads = [threading.Thread(target=run, args=(p,)) for p in patients]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert sorted(outcomes) == ["booked", "taken"]
    assert _live_at(engine, start_at) == 1
    with engine.connect() as conn:
        claimed = conn.execute(
            text(f"SELECT slot_claimed_at FROM {PLATFORM_SCHEMA}.booking_links WHERE slug = :s"),  # noqa: S608
            {"s": slug},
        ).scalar_one()
    assert claimed is not None


def test_a_later_use_is_refused_as_taken(engine: Engine, tenant: None) -> None:
    start_at = _DAY.replace(hour=17)
    slug = _seed_link(engine, start_at)
    with _owner_session(engine) as s:
        first, second = seed_patient_row(s, _OWNER), seed_patient_row(s, _OWNER)
        s.commit()

    outcomes: list[str] = []
    _visit(engine, slug, first, outcomes)
    _visit(engine, slug, second, outcomes)

    assert outcomes == ["booked", "taken"]
    assert _live_at(engine, start_at) == 1
