# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Integration test: the public booking dependency routes to the right tenant.

``get_public_booking_context`` (backend/app/routes/public_booking.py) is the
one seam every anonymous booking-link request passes through before it can
touch a patient or an appointment: it resolves a slug, switches the request
session's search_path to the owning practice's schema, and arms the RLS
clinician GUC as the link's owner. This test provisions two real practice
schemas from the canonical tenant template, gives each a live booking link
and owner, and drives that dependency directly (plus the repositories and
services a booking POST uses) to check search_path, appointment reads, and
appointment/patient writes all land in the resolved practice's own schema
and nowhere else.

RLS is not under test here (see test_tenant_isolation.py /
test_patient_guc_integration.py for that); this suite is only about which
schema a request ends up in and where its writes land. It still runs under
RLS, though: the suite's role is NOSUPERUSER NOBYPASSRLS (tests_integration/
conftest.py), so the out-of-band row counts arm ``app.current_user_id`` as
the clinician whose write is being located — an unarmed connection sees
nothing under FORCE ROW LEVEL SECURITY and would report every schema empty.

Requires:
  - Cloud SQL proxy running (make db-dev-proxy) or local Postgres (make db-up)
  - DATABASE_BACKEND=postgres
  - DATABASE_URL=postgresql://...

Run: make test-integration
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.api_errors import NotFoundError
from app.db import PLATFORM_SCHEMA, _request_session, arm_current_user_id, set_tenant_schema
from app.db.platform_models import BookingLinkRow, PlatformUserRow, PracticeRow
from app.db.provisioning import create_practice_schema
from app.models.patient import Patient
from app.repositories import (
    get_appointment_repository,
    get_availability_rule_repository,
    get_booking_link_repository,
    get_user_repository,
)
from app.repositories.postgres.appointment import PostgresAppointmentRepository
from app.repositories.postgres.availability_rule import PostgresAvailabilityRuleRepository
from app.repositories.postgres.patient import PostgresPatientRepository
from app.routes.public_booking import get_public_booking_context
from app.scheduling_engine.models.availability import AvailabilityRule, EnforcementLevel, RuleType
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.settings import get_settings
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from app.scheduling_engine.repositories.appointment import AppointmentRepository

# Skip entire module if no Postgres connection available.
_db_url = os.environ.get("DATABASE_URL", "")
_skip_reason = (
    "PostgreSQL not configured. Set DATABASE_URL and DATABASE_BACKEND=postgres. "
    "Start proxy with: make db-dev-proxy"
)
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=_skip_reason,
)

_SUFFIX = uuid.uuid4().hex[:8]
SCHEMA_A = f"practice_test_pb_a_{_SUFFIX}"
SCHEMA_B = f"practice_test_pb_b_{_SUFFIX}"

_OWNER_A = str(uuid.uuid4())
_OWNER_B = str(uuid.uuid4())
_PRACTICE_A = f"practice-row-a-{_SUFFIX}"
_PRACTICE_B = f"practice-row-b-{_SUFFIX}"
_LINK_A = str(uuid.uuid4())
_LINK_B = str(uuid.uuid4())
_LINK_ORPHAN = str(uuid.uuid4())
_SLUG_A = f"slug-a-{_SUFFIX}"
_SLUG_B = f"slug-b-{_SUFFIX}"
_SLUG_ORPHAN = f"slug-orphan-{_SUFFIX}"

# A fixed future weekday (well within MAX_ADVANCE_DAYS) so the working-hours
# rule and the free-slots query always agree on which day of week it is.
_BOOKING_DATE = (datetime.now(UTC) + timedelta(days=3)).date()


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture(scope="module")
def engine():
    """A SQLAlchemy engine connected to real Postgres, independent of the
    app's own cached engine (mirrors test_tenant_isolation.py)."""
    return create_engine(_db_url, pool_pre_ping=True)


@pytest.fixture(scope="module", autouse=True)
def _multi_tenancy_enabled():
    """Force ``multi_tenancy_enabled`` on for this module's tests only.

    ``get_settings`` is a process-wide ``lru_cache``, and sibling integration
    modules in this same directory (test_tenant_session_integration.py,
    test_patient_guc_integration.py) rely on it reading *false*. Setting the
    env var at import time would leak into whichever of those runs next in
    the same pytest process, so this restores the prior value (and clears
    the cache again) on the way out rather than just setting and forgetting.
    """
    previous = os.environ.get("MULTI_TENANCY_ENABLED")
    os.environ["MULTI_TENANCY_ENABLED"] = "true"
    get_settings.cache_clear()
    yield
    if previous is None:
        os.environ.pop("MULTI_TENANCY_ENABLED", None)
    else:
        os.environ["MULTI_TENANCY_ENABLED"] = previous
    get_settings.cache_clear()


def _make_working_hours_rule(user_id: str) -> AvailabilityRule:
    """An all-day rule for ``_BOOKING_DATE``'s weekday, so free-slots has
    something to offer without needing a realistic clinic schedule."""
    now = _now()
    return AvailabilityRule(
        id=str(uuid.uuid4()),
        user_id=user_id,
        rule_type=RuleType.WORKING_HOURS.value,
        enforcement=EnforcementLevel.SOFT.value,
        params={"day_of_week": _BOOKING_DATE.weekday(), "start": "00:00", "end": "23:59"},
        created_at=now,
        updated_at=now,
    )


@pytest.fixture(scope="module")
def two_practices(engine):
    """Provision two full tenant schemas from the canonical template, each
    with a platform-registered practice, an owning clinician, a live
    booking link, and an all-day availability rule.

    Also registers a third, orphaned link whose practice_id is NULL —
    the "booking link outlived its practice row" shape the negative test
    exercises.
    """
    create_practice_schema(engine, SCHEMA_A)
    create_practice_schema(engine, SCHEMA_B)

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    platform_session = session_factory()
    try:
        for owner_id, email, schema, practice_id, name in (
            (_OWNER_A, f"owner-a-{_SUFFIX}@example.test", SCHEMA_A, _PRACTICE_A, "Alpha Practice"),
            (_OWNER_B, f"owner-b-{_SUFFIX}@example.test", SCHEMA_B, _PRACTICE_B, "Beta Practice"),
        ):
            platform_session.add(
                PlatformUserRow(
                    id=owner_id,
                    email=email,
                    name="Practice Owner",
                    created_at=_now(),
                    status="approved",
                    is_platform_admin=False,
                    chat_quality_review_opt_in=False,
                    session_notes_quality_review_opt_in=False,
                )
            )
            platform_session.add(
                PracticeRow(
                    id=practice_id,
                    name=name,
                    schema_name=schema,
                    owner_email=email,
                    owner_user_id=owner_id,
                    created_at=_now(),
                )
            )
        platform_session.flush()

        for link_id, slug, owner_id, practice_id in (
            (_LINK_A, _SLUG_A, _OWNER_A, _PRACTICE_A),
            (_LINK_B, _SLUG_B, _OWNER_B, _PRACTICE_B),
        ):
            platform_session.add(
                BookingLinkRow(
                    id=link_id,
                    slug=slug,
                    user_id=owner_id,
                    practice_id=practice_id,
                    host_name="Test Host",
                    title="Consultation",
                    duration_minutes=30,
                    session_type="individual",
                    is_active=True,
                    created_at=_now(),
                    updated_at=_now(),
                )
            )
        # An orphan link: no practice row at all, so get_by_slug's outer
        # join resolves practice_schema to NULL even though the link
        # itself is active.
        platform_session.add(
            BookingLinkRow(
                id=_LINK_ORPHAN,
                slug=_SLUG_ORPHAN,
                user_id=_OWNER_A,
                practice_id=None,
                host_name="Orphan Host",
                title="Consultation",
                duration_minutes=30,
                session_type="individual",
                is_active=True,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        platform_session.commit()
    finally:
        platform_session.close()

    tenant_session = session_factory()
    try:
        for owner_id, schema in ((_OWNER_A, SCHEMA_A), (_OWNER_B, SCHEMA_B)):
            set_tenant_schema(tenant_session, schema)
            arm_current_user_id(tenant_session, owner_id)
            PostgresAvailabilityRuleRepository(tenant_session).create(
                _make_working_hours_rule(owner_id)
            )
        tenant_session.commit()
    finally:
        tenant_session.close()

    yield

    with engine.connect() as conn:
        conn.execute(
            text(f"DELETE FROM {PLATFORM_SCHEMA}.booking_links WHERE id::text = ANY(:ids)"),  # noqa: S608
            {"ids": [_LINK_A, _LINK_B, _LINK_ORPHAN]},
        )
        conn.execute(
            text(f"DELETE FROM {PLATFORM_SCHEMA}.practices WHERE id = ANY(:ids)"),  # noqa: S608
            {"ids": [_PRACTICE_A, _PRACTICE_B]},
        )
        conn.execute(
            text(f"DELETE FROM {PLATFORM_SCHEMA}.users WHERE id::text = ANY(:ids)"),  # noqa: S608
            {"ids": [_OWNER_A, _OWNER_B]},
        )
        for schema in (SCHEMA_A, SCHEMA_B):
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        conn.commit()


@pytest.fixture
def pg_session(engine, two_practices):
    """A request-scoped session, published on the same contextvar
    ``get_db_session()`` reads, so ``get_public_booking_context`` and the
    repository factories all resolve to the one connection this test
    controls (mirrors db_alpha/db_beta in test_tenant_isolation.py)."""
    conn = engine.connect()
    session = sessionmaker(bind=conn, expire_on_commit=False)()
    token = _request_session.set(session)
    yield session
    _request_session.reset(token)
    session.rollback()
    session.close()
    conn.close()


def _count_as(engine, schema: str, table: str, as_user: str) -> int:
    """Rows of ``schema.table`` visible to ``as_user``, on a fresh connection.

    Armed as the clinician whose write we are locating, so pointing this at
    the *other* schema is what exposes a misplaced row: a booking that
    landed in B's schema would still carry A's owner as ``user_id`` and be
    visible to nobody else.
    """
    with engine.connect() as conn:
        conn.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": as_user})
        return conn.execute(text(f"SELECT count(*) FROM {schema}.{table}")).scalar_one()  # noqa: S608


def _appointment_count(engine, schema: str, as_user: str) -> int:
    return _count_as(engine, schema, "appointments", as_user)


def _patient_count(engine, schema: str, as_user: str) -> int:
    return _count_as(engine, schema, "patients", as_user)


def _book_instant(
    session: Session, owner_id: str, start_at: datetime, duration_minutes: int = 30
) -> None:
    """Recreate the essential writes of create_public_booking's instant
    path: a fresh patient, then a confirmed appointment for it.

    Both repositories are built directly off ``session`` (not the
    ``get_*_repository`` factories, which resolve whatever session is
    currently published on the request-scoped contextvar) so this helper
    writes through exactly the connection its caller armed, regardless of
    whether that connection happens to be the published one.
    """
    patient_repo = PostgresPatientRepository(session)
    patient = patient_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Booker",
            last_name="Test",
            email=f"booker-{uuid.uuid4().hex[:8]}@example.test",
            origin="public_booking",
            created_at=_now(),
            updated_at=_now(),
        ),
        owner_id,
    )
    appt_repo: AppointmentRepository = PostgresAppointmentRepository(session)
    scheduling = SchedulingService(appt_repo)
    scheduling.create_appointment(
        owner_id,
        data={
            "patient_id": patient.id,
            "title": "Consultation",
            "start_at": start_at,
            "end_at": start_at + timedelta(minutes=duration_minutes),
            "duration_minutes": duration_minutes,
            "session_type": "individual",
        },
    )
    session.commit()


class TestSchemaRouting:
    """get_public_booking_context must land the session on the link's own
    schema and arm RLS as the link's owner -- nothing else."""

    def test_search_path_lands_on_owning_schema(self, pg_session):
        link_repo = get_booking_link_repository()
        user_repo = get_user_repository()

        ctx = get_public_booking_context(_SLUG_A, link_repo=link_repo, user_repo=user_repo)

        assert ctx.link.practice_schema == SCHEMA_A
        assert ctx.owner.id == _OWNER_A

        search_path = pg_session.execute(text("SHOW search_path")).scalar()
        assert SCHEMA_A in search_path
        assert SCHEMA_B not in search_path

    def test_current_user_id_armed_to_owner(self, pg_session):
        link_repo = get_booking_link_repository()
        user_repo = get_user_repository()

        get_public_booking_context(_SLUG_A, link_repo=link_repo, user_repo=user_repo)

        armed = pg_session.execute(
            text("SELECT current_setting('app.current_user_id', true)")
        ).scalar()
        assert armed == _OWNER_A

    def test_null_practice_schema_is_not_found(self, pg_session):  # noqa: ARG002 — fixture arms the request session
        link_repo = get_booking_link_repository()
        user_repo = get_user_repository()

        with pytest.raises(NotFoundError):
            get_public_booking_context(_SLUG_ORPHAN, link_repo=link_repo, user_repo=user_repo)


class TestFreeSlotsScoping:
    """Free-slots reads must come from the resolved schema's own
    appointments table, not from a booking with the same wall-clock time
    sitting in a different tenant's schema."""

    def test_free_slots_ignore_other_tenants_appointment(self, engine, pg_session):  # noqa: ARG002 — fixture arms the request session
        collision_start = datetime.combine(
            _BOOKING_DATE, datetime.min.time(), tzinfo=UTC
        ) + timedelta(hours=9)

        # A real, legitimately-armed booking for B's own owner at the exact
        # wall-clock slot A is about to be asked about.
        beta_session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        beta_session = beta_session_factory()
        try:
            set_tenant_schema(beta_session, SCHEMA_B)
            arm_current_user_id(beta_session, _OWNER_B)
            _book_instant(beta_session, _OWNER_B, collision_start)
        finally:
            beta_session.close()

        link_repo = get_booking_link_repository()
        user_repo = get_user_repository()
        ctx = get_public_booking_context(_SLUG_A, link_repo=link_repo, user_repo=user_repo)

        engine_svc = AvailabilityEngine(
            get_availability_rule_repository(), get_appointment_repository()
        )
        result = engine_svc.get_free_slots(
            ctx.link.user_id, _BOOKING_DATE.isoformat(), ctx.link.duration_minutes
        )

        assert result.configured is True
        collision_iso = collision_start.strftime("%Y-%m-%dT%H:%M:%SZ")
        assert any(s.start == collision_iso for s in result.slots), (
            "B's appointment at the same wall-clock time leaked into A's free slots"
        )


class TestBookingWritePlacement:
    """A booking write through the resolved context must land its patient
    and appointment in the owning schema, and nowhere else."""

    def test_booking_writes_land_only_in_owning_schema(self, engine, pg_session):
        link_repo = get_booking_link_repository()
        user_repo = get_user_repository()
        ctx = get_public_booking_context(_SLUG_A, link_repo=link_repo, user_repo=user_repo)
        writer = ctx.link.user_id

        before_a_appts = _appointment_count(engine, SCHEMA_A, writer)
        before_b_appts = _appointment_count(engine, SCHEMA_B, writer)
        before_a_patients = _patient_count(engine, SCHEMA_A, writer)
        before_b_patients = _patient_count(engine, SCHEMA_B, writer)

        start_at = datetime.combine(_BOOKING_DATE, datetime.min.time(), tzinfo=UTC) + timedelta(
            hours=14
        )
        _book_instant(pg_session, ctx.link.user_id, start_at, ctx.link.duration_minutes)

        assert _appointment_count(engine, SCHEMA_A, writer) == before_a_appts + 1
        assert _appointment_count(engine, SCHEMA_B, writer) == before_b_appts
        assert _patient_count(engine, SCHEMA_A, writer) == before_a_patients + 1
        assert _patient_count(engine, SCHEMA_B, writer) == before_b_patients
