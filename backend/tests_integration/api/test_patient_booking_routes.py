# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A patient booking their own appointment, against a real provisioned tenant.

The routes run as a patient principal, and the two things worth proving about
them cannot be shown against fakes:

**The slot response leaks nothing.** Free slots are computed from the WHOLE
diary — a time is free precisely because no other patient holds it — so the
computation necessarily reads every appointment in the practice. The test seeds
another patient's appointment and asserts that the hour disappears from the
offers AND that nothing about that patient, that appointment, or its owner
appears anywhere in the response body.

**A booking cannot land on a taken slot.** The conflict check runs on the
owner-armed session; on the patient's own session it would see only the
caller's rows and every hour another patient holds would look free. The test
books directly on top of another patient's appointment and requires a refusal.

Both are the "two principals on one transaction" class that the patient
principal's review found six times. They pass trivially if the route ever
collapses to one session, which is why each is paired with a control asserting
the slot IS offered when nobody holds it.

The principal is synthesized through ``dependency_overrides`` — magic-link
issuance is a separate bead, and the resolver is not what is under test here.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

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
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_SUFFIX = uuid.uuid4().hex[:8]
_SCHEMA = f"practice_test_pb_{_SUFFIX}"
_PRACTICE_ID = f"practice-pb-{_SUFFIX}"
_CLINICIAN = str(uuid.uuid4())
_SESSION_TYPE = "Therapy session"


#: The clinician's own timezone, seeded explicitly rather than left to the
#: UserPreferences default. Availability is expressed in this frame — working
#: hours, the calendar date ``get_free_slots`` takes, and therefore every slot
#: boundary — so a test that writes times in UTC is writing them in the wrong
#: frame. Deliberately not UTC: a UTC practice would make the route's timezone
#: handling untestable by making it a no-op.
_TZ_NAME = "America/New_York"
_TZ = ZoneInfo(_TZ_NAME)

#: A round working day, and an hourly alignment rule to go with it, so the
#: bookable lattice is exactly 09:00, 10:00 ... 16:00 local.
#:
#: This matters more than it looks. The engine starts at the working-hours
#: boundary and strides by duration-plus-buffers, so an all-day 00:00-23:59
#: rule with a 50-minute type puts slot starts at local 00:00, 00:50, 01:40 ...
#: — a lattice whose intersection with round hours is unguessable, and which
#: has nothing to do with what the tests mean to assert. Pinning the day and
#: the alignment makes "10:00 is bookable" a fact about the seeded rules
#: instead of an arithmetic coincidence.
_WORK_START_HOUR = 9
_WORK_END_HOUR = 17


#: Far enough out to clear any sane min_notice, close enough to sit inside a
#: 60-day horizon. Monday, so a weekday working-hours rule covers it.
def _target_date() -> date:
    """The local calendar date these tests book on."""
    day = (datetime.now(UTC) + timedelta(days=14)).astimezone(_TZ).date()
    return day + timedelta(days=(7 - day.weekday()) % 7)


def _at(hour: int, minute: int = 0) -> datetime:
    """The UTC instant of a wall-clock time in the clinician's timezone.

    Every time in this module goes through here. Writing ``.replace(hour=10)``
    on a UTC midnight instead means "10:00 UTC", which is 06:00 to the
    clinician whose availability decides whether it is bookable.
    """
    return datetime.combine(_target_date(), time(hour, minute), tzinfo=_TZ).astimezone(UTC)


def _date_param() -> str:
    """The ``date`` query parameter — a LOCAL calendar date, per the engine."""
    return _target_date().isoformat()


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_DB_URL, pool_pre_ping=True)
    yield eng
    eng.dispose()


def _seed_owner_timezone(engine: Engine) -> None:
    """Pin the clinician's timezone instead of relying on the model default.

    The route reads it to decide which local day a booking falls on. Leaving it
    implicit means these tests would start passing or failing on a change to an
    unrelated default, and would silently become a UTC-only exercise if that
    default ever moved to UTC.
    """
    from app.models.user import UserPreferences  # noqa: PLC0415
    from app.repositories.postgres.user import (  # noqa: PLC0415
        PostgresUserRepository,
    )
    from sqlalchemy.orm import (  # noqa: PLC0415
        Session as OrmSession,
    )

    with OrmSession(bind=engine) as s:
        PostgresUserRepository(s).save_preferences(_CLINICIAN, UserPreferences(timezone=_TZ_NAME))
        s.commit()


def _seed_working_hours(engine: Engine) -> None:
    """An all-day rule on the target weekday.

    Written through the repository rather than as raw SQL: the rule's shape
    lives in a JSONB ``params`` column, and a hand-written INSERT here would be
    a second, silently-drifting copy of that schema.
    """
    from app.db import (  # noqa: PLC0415
        arm_current_user_id,
        set_tenant_schema,
    )
    from app.repositories.postgres.availability_rule import (  # noqa: PLC0415
        PostgresAvailabilityRuleRepository,
    )
    from app.scheduling_engine.models.availability import (  # noqa: PLC0415
        AvailabilityRule,
        EnforcementLevel,
        RuleType,
    )
    from sqlalchemy.orm import (  # noqa: PLC0415
        Session as OrmSession,
    )

    now = datetime.now(UTC)
    with OrmSession(bind=engine) as s:
        set_tenant_schema(s, _SCHEMA)
        arm_current_user_id(s, _CLINICIAN)
        repo = PostgresAvailabilityRuleRepository(s)
        for rule_type, params in (
            (
                RuleType.WORKING_HOURS,
                {
                    "day_of_week": _target_date().weekday(),
                    "start": f"{_WORK_START_HOUR:02d}:00",
                    "end": f"{_WORK_END_HOUR:02d}:00",
                },
            ),
            # Without this the stride is the raw duration and slot starts walk
            # off the hour after the first one. With it the lattice is the set
            # of round local hours inside the working day, which is what the
            # tests below assume and now actually get.
            (RuleType.SESSION_DEFAULTS, {"alignment": "hour"}),
        ):
            repo.create(
                AvailabilityRule(
                    id=str(uuid.uuid4()),
                    user_id=_CLINICIAN,
                    rule_type=rule_type.value,
                    enforcement=EnforcementLevel.SOFT.value,
                    params=params,
                    created_at=now,
                    updated_at=now,
                )
            )
        s.commit()


@pytest.fixture(scope="module")
def practice(engine: Engine) -> Iterator[dict[str, Any]]:
    """A provisioned practice with an owner, two patients, hours and a type."""
    from app.db.platform_models import (  # noqa: PLC0415
        PlatformUserRow,
        PracticeRow,
    )
    from app.db.provisioning import (  # noqa: PLC0415
        create_practice_schema,
    )
    from sqlalchemy.orm import (  # noqa: PLC0415
        Session as OrmSession,
    )

    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()
    create_practice_schema(engine, _SCHEMA)

    now = datetime.now(UTC)
    with OrmSession(bind=engine) as s:
        s.add(
            PlatformUserRow(
                id=_CLINICIAN, email=f"{_PRACTICE_ID}@example.test", name="Owner", created_at=now
            )
        )
        s.add(
            PracticeRow(
                id=_PRACTICE_ID,
                name="Booking Test Practice",
                schema_name=_SCHEMA,
                owner_email=f"{_PRACTICE_ID}@example.test",
                owner_user_id=_CLINICIAN,
                created_at=now,
            )
        )
        s.commit()

    patient_a, patient_b = str(uuid.uuid4()), str(uuid.uuid4())
    type_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
        for pid, first in ((patient_a, "Ada"), (patient_b, "Grace")):
            conn.execute(
                text(
                    "INSERT INTO patients (id, first_name, last_name, first_name_lower, "
                    "last_name_lower, status, session_count, created_at, updated_at) "
                    "VALUES (CAST(:p AS uuid), :f, 'Tester', lower(:f), 'tester', 'active', "
                    "0, now(), now())"
                ),
                {"p": pid, "f": first},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                    "VALUES (CAST(:p AS uuid), :u, :u)"
                ),
                {"p": pid, "u": _CLINICIAN},
            )
        conn.execute(
            text(
                "INSERT INTO appointment_types (id, user_id, name, duration_minutes, "
                "self_bookable, created_at, updated_at) "
                "VALUES (CAST(:i AS uuid), CAST(:u AS uuid), :n, 50, true, now(), now())"
            ),
            {"i": type_id, "u": _CLINICIAN, "n": _SESSION_TYPE},
        )
    _seed_owner_timezone(engine)
    _seed_working_hours(engine)

    yield {"schema": _SCHEMA, "a": patient_a, "b": patient_b, "type_id": type_id}

    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE'))
        conn.execute(text("DELETE FROM platform.practices WHERE id = :i"), {"i": _PRACTICE_ID})
        conn.execute(
            text("DELETE FROM platform.users WHERE id = CAST(:i AS uuid)"), {"i": _CLINICIAN}
        )


def _set_policy(engine: Engine, **fields: Any) -> None:
    """Write the practice's scheduling policy, upserting the singleton row."""
    from app.scheduling_engine.services.scheduling_policy import (  # noqa: PLC0415
        update_policy,
    )
    from sqlalchemy.orm import (  # noqa: PLC0415
        Session as OrmSession,
    )

    with OrmSession(bind=engine) as s:
        s.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        s.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
        update_policy(s, fields)
        s.commit()


def _client(  # type: ignore[no-untyped-def]
    practice: dict[str, Any], patient_id: str, *, stepped_up: bool = True
):
    """A TestClient whose patient principal is the given id.

    ``dependency_overrides`` rather than a real magic link: issuance is its own
    bead, and the resolver is not what these tests are about. What matters is
    that the routes receive a PatientContext of the same shape the real
    resolver produces — including ``auth_strength``, which the write routes
    enforce.
    """
    from app.auth.patient_context import (  # noqa: PLC0415
        AuthStrength,
        PatientContext,
        get_patient_context,
    )
    from app.auth.route_access import (  # noqa: PLC0415
        subscription_exempt,
    )
    from app.db import (  # noqa: PLC0415
        arm_current_patient_id,
        get_db_session,
        set_tenant_schema,
    )
    from app.main import app  # noqa: PLC0415
    from fastapi.testclient import (  # noqa: PLC0415
        TestClient,
    )

    strength = AuthStrength.STEPPED_UP if stepped_up else AuthStrength.SINGLE_FACTOR

    def _context() -> PatientContext:
        """Stand in for the resolver AND for what it arms.

        Returning a bare context is not enough. The real dependency also puts
        the request's session into the tenant schema and arms
        ``app.current_patient_id`` on it — so an override that skips that leaves
        every route running against the template schema with no principal, and
        the audit INSERT lands nowhere. That is not a smaller version of
        production; it is a shape production cannot produce, and a test built on
        it proves nothing about either.
        """
        context = PatientContext(
            patient_id=patient_id,
            practice_schema=practice["schema"],
            credential_kind="bearer",
            auth_strength=strength,
        )
        session = get_db_session()
        set_tenant_schema(session, context.practice_schema)
        arm_current_patient_id(session, context.patient_id)
        return context

    app.dependency_overrides[get_patient_context] = _context
    app.dependency_overrides[subscription_exempt] = lambda: None
    # https, because SecurityHeadersMiddleware refuses plain HTTP outright —
    # these routes carry appointment times, which is PHI. Not overridden away:
    # a test client that quietly bypassed the transport rule would be testing a
    # deployment shape that does not exist.
    client = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def patient_a_client(practice: dict[str, Any]):  # type: ignore[no-untyped-def]
    yield from _client(practice, practice["a"])


@pytest.fixture
def single_factor_client(practice: dict[str, Any]):  # type: ignore[no-untyped-def]
    """Patient A, holding only what an unredeemed invite link proves."""
    yield from _client(practice, practice["a"], stepped_up=False)


def _seed_appointment(engine: Engine, patient_id: str, start: datetime) -> str:
    """An appointment on the clinician's diary, booked clinician-side.

    Through the real ``SchedulingService`` rather than an INSERT. ``appointments``
    carries NOT NULL columns whose defaults are Python-side (``is_exception``
    among them), so hand-written SQL needs editing every time one lands — and
    more to the point, a fixture that writes rows the application could not
    produce is testing against a database state that cannot occur.
    """
    from app.db import (  # noqa: PLC0415
        arm_current_user_id,
        set_tenant_schema,
    )
    from app.repositories.postgres.appointment import (  # noqa: PLC0415
        PostgresAppointmentRepository,
    )
    from app.scheduling_engine.services.scheduling import (  # noqa: PLC0415
        SchedulingService,
    )
    from sqlalchemy.orm import (  # noqa: PLC0415
        Session as OrmSession,
    )

    with OrmSession(bind=engine) as s:
        set_tenant_schema(s, _SCHEMA)
        arm_current_user_id(s, _CLINICIAN)
        created = SchedulingService(PostgresAppointmentRepository(s)).create_appointment(
            _CLINICIAN,
            data={
                "patient_id": patient_id,
                "title": "Booked",
                "start_at": start,
                "end_at": start + timedelta(minutes=50),
                "duration_minutes": 50,
                "session_type": _SESSION_TYPE,
            },
        )
        s.commit()
    return created.id


def _starts(response: Any) -> set[datetime]:
    """The offered start instants, parsed rather than string-matched."""
    return {
        datetime.fromisoformat(s["start_at"].replace("Z", "+00:00")).astimezone(UTC)
        for s in response.json()["data"]
    }


def _clear_appointments(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("DELETE FROM appointments"))


# ---------------------------------------------------------------------------
# Dark by default
# ---------------------------------------------------------------------------


def test_slots_are_refused_until_the_practice_turns_self_booking_on(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """A practice that has not decided has not agreed.

    403 rather than an empty list: "you may not" and "there is nothing free"
    are different answers and a patient should be able to tell them apart.
    """
    _set_policy(engine, self_book_existing=False)

    response = patient_a_client.get("/api/patient/booking/slots", params={"date": _date_param()})

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "SELF_BOOKING_DISABLED"


def test_booking_is_refused_until_the_practice_turns_self_booking_on(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    _set_policy(engine, self_book_existing=False)
    start = _at(10)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={"start_at": start.isoformat(), "session_type": _SESSION_TYPE},
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "SELF_BOOKING_DISABLED"


def test_a_single_factor_principal_cannot_book(
    engine: Engine, practice: dict[str, Any], single_factor_client: Any
) -> None:
    """An invite link that reached the wrong inbox is one factor in a
    stranger's hands, and booking writes to a real clinician's calendar.

    Checked BEFORE the policy gate, so a practice with self-booking switched on
    is exactly where this matters — a test that only passed while booking was
    disabled would be proving nothing.
    """
    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="auto",
        min_notice_hours=1,
        max_horizon_days=60,
    )

    response = single_factor_client.post(
        "/api/patient/booking",
        json={
            "start_at": _at(9).isoformat(),
            "session_type": _SESSION_TYPE,
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "STEP_UP_REQUIRED"


# ---------------------------------------------------------------------------
# The leak test
# ---------------------------------------------------------------------------


def test_a_slot_another_patient_holds_is_absent_and_unmentioned(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """The one that matters. Free slots are computed from the whole diary, so
    the computation reads patient B's appointment; the response must carry no
    trace of it."""
    _set_policy(engine, self_book_existing=True, min_notice_hours=1, max_horizon_days=60)
    _clear_appointments(engine)
    taken = _at(10)

    date = _date_param()
    before = patient_a_client.get("/api/patient/booking/slots", params={"date": date})
    assert before.status_code == 200, before.text
    offered_before = _starts(before)
    # Control: without it, "absent" below would prove nothing — an empty list
    # passes every absence assertion ever written.
    assert taken in offered_before, (
        f"the {taken:%H:%M} slot was never offered even when free — the fixture "
        f"is wrong, not the route. Offered: {sorted(offered_before)[:6]}"
    )

    _seed_appointment(engine, practice["b"], taken)

    after = patient_a_client.get("/api/patient/booking/slots", params={"date": date})
    assert after.status_code == 200, after.text
    body = after.text
    offered_after = _starts(after)

    # The exact instant, not the hour: a 50-minute appointment at 10:00 leaves
    # 10:50 genuinely free, and an hour-prefix match would call that a leak.
    assert taken not in offered_after, (
        f"the {taken:%H:%M} slot another patient holds was offered as free"
    )
    # And nothing about who holds it.
    for leaked in (practice["b"], _CLINICIAN, "Grace", "Booked", "confirmed", "patient_id"):
        assert leaked not in body, f"slot response leaked {leaked!r}: {body[:400]}"


def test_the_slot_response_carries_only_times(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """Field-level, not just substring: the shape itself must be three keys."""
    _set_policy(engine, self_book_existing=True, min_notice_hours=1, max_horizon_days=60)
    _clear_appointments(engine)

    response = patient_a_client.get("/api/patient/booking/slots", params={"date": _date_param()})

    assert response.status_code == 200, response.text
    slots = response.json()["data"]
    assert slots, "no slots offered — the fixture's working hours are wrong"
    for slot in slots:
        assert set(slot) == {"start_at", "end_at", "duration_minutes"}, slot


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------


def test_a_booking_lands_pending_in_request_mode(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """``request`` is the default: the practice sees a request, not a fait
    accompli."""
    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="request",
        min_notice_hours=1,
        max_horizon_days=60,
    )
    _clear_appointments(engine)
    start = _at(11)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={"start_at": start.isoformat(), "session_type": _SESSION_TYPE},
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"


def test_auto_mode_books_it_outright(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="auto",
        min_notice_hours=1,
        max_horizon_days=60,
    )
    _clear_appointments(engine)
    start = _at(12)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={"start_at": start.isoformat(), "session_type": _SESSION_TYPE},
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "confirmed"


def test_a_booking_cannot_land_on_another_patients_appointment(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """The double-booking test. Under the patient GUC the conflict check would
    see none of patient B's rows and this would succeed."""
    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="auto",
        min_notice_hours=1,
        max_horizon_days=60,
    )
    _clear_appointments(engine)
    taken = _at(13)
    _seed_appointment(engine, practice["b"], taken)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={"start_at": taken.isoformat(), "session_type": _SESSION_TYPE},
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"]["code"] == "SLOT_TAKEN"
    # And it says nothing about who holds it.
    assert practice["b"] not in response.text


def test_a_time_the_engine_would_never_offer_is_refused(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """The one the other tests could not catch.

    Every booking test above books a slot the listing endpoint offered, so all
    of them pass whether or not the route enforces anything: the listing already
    applied the rules. This posts a time the engine would never return — 03:00,
    far outside the seeded working hours — and requires a refusal.

    Without the availability engine wired into ``SchedulingService`` the only
    check on a booking is a raw overlap test, so 03:00 is free and this returns
    201. Every rule type at both enforcement levels is bypassed the same way:
    working hours, blocked days, blocked date ranges, per-day caps, buffers.
    """
    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="auto",
        min_notice_hours=1,
        max_horizon_days=60,
    )
    _clear_appointments(engine)
    # 03:00 sits inside the 00:00-23:59 all-day rule the fixture seeds, so this
    # would pass a naive "is it within working hours" check — what refuses it is
    # that the engine does not offer 03:00 as a slot boundary for this duration.
    outside = _at(3, 7)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={"start_at": outside.isoformat(), "session_type": _SESSION_TYPE},
    )

    assert response.status_code == 409, (
        f"a time the engine never offered was booked anyway: {response.text}"
    )
    assert response.json()["detail"]["error"]["code"] == "SLOT_TAKEN"


def test_a_blocked_day_cannot_be_booked(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """A hard ``block_day_of_week`` must refuse, not merely fail to offer.

    The clinician has said "not that day". A patient posting a time on it
    directly has to hit the same wall the listing does — this is the rule most
    likely to be tested by someone who read the slots endpoint and then guessed
    a time.
    """
    from app.db import arm_current_user_id, set_tenant_schema  # noqa: PLC0415
    from app.repositories.postgres.availability_rule import (  # noqa: PLC0415
        PostgresAvailabilityRuleRepository,
    )
    from app.scheduling_engine.models.availability import (  # noqa: PLC0415
        AvailabilityRule,
        EnforcementLevel,
        RuleType,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="auto",
        min_notice_hours=1,
        max_horizon_days=60,
    )
    _clear_appointments(engine)

    blocked_rule_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    with OrmSession(bind=engine) as s:
        set_tenant_schema(s, _SCHEMA)
        arm_current_user_id(s, _CLINICIAN)
        PostgresAvailabilityRuleRepository(s).create(
            AvailabilityRule(
                id=blocked_rule_id,
                user_id=_CLINICIAN,
                rule_type=RuleType.BLOCK_DAY_OF_WEEK.value,
                enforcement=EnforcementLevel.HARD.value,
                params={"day_of_week": _target_date().weekday()},
                created_at=now,
                updated_at=now,
            )
        )
        s.commit()

    try:
        response = patient_a_client.post(
            "/api/patient/booking",
            json={
                "start_at": _at(11).isoformat(),
                "session_type": _SESSION_TYPE,
            },
        )
        assert response.status_code == 409, (
            f"a day the clinician blocked was booked anyway: {response.text}"
        )
    finally:
        with OrmSession(bind=engine) as s:
            set_tenant_schema(s, _SCHEMA)
            arm_current_user_id(s, _CLINICIAN)
            PostgresAvailabilityRuleRepository(s).delete(blocked_rule_id, _CLINICIAN)
            s.commit()


def test_a_type_nobody_opted_in_is_not_bookable(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """Allow-list, not deny-list: turning the master switch on opens nothing by
    itself."""
    _set_policy(engine, self_book_existing=True, min_notice_hours=1, max_horizon_days=60)
    _clear_appointments(engine)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={
            "start_at": _at(14).isoformat(),
            "session_type": "Internal admin block",
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["error"]["code"] == "TYPE_NOT_BOOKABLE"


def test_a_time_inside_the_notice_window_is_refused(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    _set_policy(engine, self_book_existing=True, min_notice_hours=48, max_horizon_days=60)
    _clear_appointments(engine)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={
            "start_at": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            "session_type": _SESSION_TYPE,
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"]["code"] == "INSIDE_NOTICE_WINDOW"


def test_a_date_beyond_the_horizon_is_refused(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    _set_policy(engine, self_book_existing=True, min_notice_hours=1, max_horizon_days=7)
    _clear_appointments(engine)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={
            "start_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
            "session_type": _SESSION_TYPE,
        },
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["error"]["code"] == "OUTSIDE_HORIZON"


# ---------------------------------------------------------------------------
# IDOR
# ---------------------------------------------------------------------------


def test_a_smuggled_patient_id_books_for_the_caller(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """The request model has no ``patient_id`` field, so an extra key is
    ignored rather than honoured — asserted, because "the model does not
    declare it" is a property that a future ``model_config`` change could
    quietly reverse."""
    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="auto",
        min_notice_hours=1,
        max_horizon_days=60,
    )
    _clear_appointments(engine)
    start = _at(15)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={
            "start_at": start.isoformat(),
            "session_type": _SESSION_TYPE,
            "patient_id": practice["b"],
        },
    )

    assert response.status_code == 201, response.text
    appointment_id = response.json()["id"]

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
        owner = conn.execute(
            text("SELECT patient_id FROM appointments WHERE id = CAST(:i AS uuid)"),
            {"i": appointment_id},
        ).scalar_one()

    assert str(owner) == practice["a"], "a smuggled patient_id booked for somebody else"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_a_booking_is_audited_as_the_patient(
    engine: Engine, practice: dict[str, Any], patient_a_client: Any
) -> None:
    """§164.312(b): the patient acted, so the row must say so — and say it was
    them, not the clinician whose diary it landed in."""
    _set_policy(
        engine,
        self_book_existing=True,
        self_book_mode="auto",
        min_notice_hours=1,
        max_horizon_days=60,
    )
    _clear_appointments(engine)
    start = _at(16)

    response = patient_a_client.post(
        "/api/patient/booking",
        json={"start_at": start.isoformat(), "session_type": _SESSION_TYPE},
    )
    assert response.status_code == 201, response.text

    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {_SCHEMA}, platform, public"))
        # The audit read policy gates on ``app.current_user_id`` whoever the
        # actor was — ``user_id`` holds the actor, and on this path that is the
        # patient. Arming the patient GUC instead would read nothing, which is
        # exactly what the policy intends for a patient principal at runtime.
        conn.execute(
            text("SELECT set_config('app.current_user_id', :p, false)"),
            {"p": practice["a"]},
        )
        rows = conn.execute(
            text(
                "SELECT actor_type, user_id FROM audit_logs "
                "WHERE resource_id = :r AND actor_type = 'patient'"
            ),
            {"r": response.json()["id"]},
        ).fetchall()

    assert rows, "the booking wrote no patient-actor audit row"
    assert str(rows[0][1]) == practice["a"], "the audit row names somebody other than the patient"
