# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for public booking links: owner-facing CRUD and the public surface.

The public router is flag-gated in the real app (PUBLIC_BOOKING_ENABLED,
default off), so — same as the launch-intent tests — the public tests
mount the router on a bare FastAPI app and override its dependencies
with in-memory repositories. The management routes are always mounted
and use the standard ``client`` fixture.
"""

from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.api_errors import register_exception_handlers
from app.main import app as real_app
from app.models import Patient, User
from app.models.booking_link import BookingLink
from app.rate_limit import (
    require_public_booking_rate_limit,
    require_public_booking_write_rate_limit,
)
from app.repositories import (
    get_booking_link_repository,
    get_patient_repository,
    get_user_repository,
)
from app.repositories.booking_link import InMemoryBookingLinkRepository
from app.repositories.patient import InMemoryPatientRepository
from app.routes import public_booking as public_booking_module
from app.routes.booking_links import get_link_repository
from app.routes.public_booking import (
    get_public_availability_engine,
    get_public_gcal_service,
    get_public_scheduling_service,
)
from app.routes.public_booking import (
    router as public_router,
)
from app.scheduling_engine.models.availability import AvailabilityRule, RuleType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services import get_audit_service
from app.utcnow import utc_now
from fastapi import FastAPI
from fastapi.testclient import TestClient

OWNER_ID = "test-user-123"


def _link(
    *,
    slug: str = "intro-call",
    user_id: str = OWNER_ID,
    is_active: bool = True,
    duration_minutes: int = 30,
) -> BookingLink:
    now = utc_now()
    return BookingLink(
        id=str(uuid.uuid4()),
        slug=slug,
        user_id=user_id,
        practice_id=None,
        host_name="Test Therapist",
        title="Intro call",
        description="A get-to-know-you call.",
        duration_minutes=duration_minutes,
        session_type="individual",
        is_active=is_active,
        created_at=now,
        updated_at=now,
    )


def _owner() -> User:
    return User(
        id=OWNER_ID,
        email="therapist@example.com",
        name="Test Therapist",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
    )


class _FakeUserRepo:
    def __init__(self, users: dict[str, User]) -> None:
        self._users = users

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)


class _FakeAudit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def log_patient_action(
        self, action: Any, user: Any, request: Any, patient: Any, changes: Any = None
    ) -> None:
        self.calls.append({"action": action, "patient": patient, "changes": changes})

    def log_appointment_action(
        self,
        action: Any,
        user: Any,
        request: Any,
        appointment_id: str,
        patient_id: str | None = None,
        changes: Any = None,
    ) -> None:
        self.calls.append(
            {"action": action, "appointment_id": appointment_id, "patient_id": patient_id}
        )


def _bookable_date() -> str:
    """A date next week — inside the booking window, weekday known."""
    return (utc_now().date() + timedelta(days=7)).isoformat()


def _working_hours_rule(date_str: str) -> AvailabilityRule:
    day_of_week = datetime.fromisoformat(f"{date_str}T00:00:00+00:00").weekday()
    return AvailabilityRule(
        id=str(uuid.uuid4()),
        user_id=OWNER_ID,
        rule_type=RuleType.WORKING_HOURS,
        enforcement="hard",
        params={"day_of_week": day_of_week, "start": "09:00", "end": "11:00"},
    )


@pytest.fixture
def link_repo() -> InMemoryBookingLinkRepository:
    return InMemoryBookingLinkRepository()


@pytest.fixture
def public_client(link_repo: InMemoryBookingLinkRepository) -> Any:
    """A TestClient over an app that mounts only the public router."""
    appt_repo = InMemoryAppointmentRepository()
    rule_repo = InMemoryAvailabilityRuleRepository()
    patient_repo = InMemoryPatientRepository()
    fake_audit = _FakeAudit()

    gcal = MagicMock()
    gcal.push_appointment.return_value = None

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(public_router)
    # The booking limiters are process-global; left live they trip 429s
    # across tests. Rate limiting has its own tests in test_rate_limit.
    app.dependency_overrides[require_public_booking_rate_limit] = lambda: None
    app.dependency_overrides[require_public_booking_write_rate_limit] = lambda: None
    app.dependency_overrides[get_booking_link_repository] = lambda: link_repo
    app.dependency_overrides[get_user_repository] = lambda: _FakeUserRepo({OWNER_ID: _owner()})
    app.dependency_overrides[get_public_availability_engine] = lambda: AvailabilityEngine(
        rule_repo, appt_repo
    )
    app.dependency_overrides[get_public_scheduling_service] = lambda: SchedulingService(appt_repo)
    app.dependency_overrides[get_patient_repository] = lambda: patient_repo
    app.dependency_overrides[get_public_gcal_service] = lambda: gcal
    app.dependency_overrides[get_audit_service] = lambda: fake_audit

    client = TestClient(app)
    client.rule_repo = rule_repo  # type: ignore[attr-defined]  # test-only stash, keeps fixtures to one object
    client.patient_repo = patient_repo  # type: ignore[attr-defined]  # test-only stash
    client.audit = fake_audit  # type: ignore[attr-defined]  # test-only stash
    return client


def _book(client: Any, slug: str, start_at: str, email: str = "jane@example.com") -> Any:
    return client.post(
        f"/api/public/booking-links/{slug}/bookings",
        json={
            "start_at": start_at,
            "first_name": "Jane",
            "last_name": "Roe",
            "email": email,
        },
    )


# ---------------------------------------------------------------- public: card


def test_public_link_card_returns_display_fields(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    resp = public_client.get("/api/public/booking-links/intro-call")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "slug": "intro-call",
        "host_name": "Test Therapist",
        "title": "Intro call",
        "description": "A get-to-know-you call.",
        "duration_minutes": 30,
    }


def test_unknown_and_inactive_slugs_are_identical_404s(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link(slug="paused-link", is_active=False))
    missing = public_client.get("/api/public/booking-links/no-such-link")
    inactive = public_client.get("/api/public/booking-links/paused-link")
    assert missing.status_code == 404
    assert inactive.status_code == 404
    assert missing.json() == inactive.json()


# --------------------------------------------------------------- public: slots


def test_slots_reflect_owner_availability(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    resp = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    starts = [s["start"] for s in body["slots"]]
    assert starts == [
        f"{date_str}T09:00:00Z",
        f"{date_str}T09:30:00Z",
        f"{date_str}T10:00:00Z",
        f"{date_str}T10:30:00Z",
    ]


def test_slots_reject_out_of_window_dates(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    past = (utc_now().date() - timedelta(days=1)).isoformat()
    far = (utc_now().date() + timedelta(days=90)).isoformat()
    for bad_date in (past, far):
        resp = public_client.get(f"/api/public/booking-links/intro-call/slots?date={bad_date}")
        assert resp.status_code == 400


# ------------------------------------------------------------- public: booking


def test_booking_creates_patient_and_appointment(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    resp = _book(public_client, "intro-call", f"{date_str}T09:30:00Z")
    assert resp.status_code == 201
    body = resp.json()
    assert body["start_at"] == f"{date_str}T09:30:00Z"
    assert body["end_at"] == f"{date_str}T10:00:00Z"
    assert body["host_name"] == "Test Therapist"

    patient = public_client.patient_repo.find_by_email("jane@example.com", OWNER_ID)
    assert patient is not None
    assert patient.first_name == "Jane"

    actions = [str(c["action"]) for c in public_client.audit.calls]
    assert any("patient_created" in a for a in actions)
    assert any("appointment_created" in a for a in actions)


def test_booked_slot_is_no_longer_offered_or_bookable(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    first = _book(public_client, "intro-call", f"{date_str}T09:00:00Z")
    assert first.status_code == 201

    slots = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()
    assert f"{date_str}T09:00:00Z" not in [s["start"] for s in slots["slots"]]

    second = _book(public_client, "intro-call", f"{date_str}T09:00:00Z", email="other@example.com")
    assert second.status_code == 409


def test_repeat_booker_reuses_patient_record(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    assert _book(public_client, "intro-call", f"{date_str}T09:00:00Z").status_code == 201
    assert _book(public_client, "intro-call", f"{date_str}T10:00:00Z").status_code == 201

    _patients, total = public_client.patient_repo.list_by_user(OWNER_ID)
    assert total == 1


def test_booking_reveals_nothing_about_existing_patients(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    """No existence oracle: booking with an existing patient's email must be
    indistinguishable from booking with a fresh one — the confirmation carries
    only link-derived fields, and the existing chart is never modified by
    attacker-supplied names."""
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    now = utc_now()
    existing = public_client.patient_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Realfirst",
            last_name="Reallast",
            email="client@example.com",
            created_at=now,
            updated_at=now,
        ),
        OWNER_ID,
    )

    fresh = public_client.post(
        "/api/public/booking-links/intro-call/bookings",
        json={
            "start_at": f"{date_str}T09:00:00Z",
            "first_name": "New",
            "last_name": "Person",
            "email": "stranger@example.com",
        },
    )
    reused = public_client.post(
        "/api/public/booking-links/intro-call/bookings",
        json={
            "start_at": f"{date_str}T09:30:00Z",
            "first_name": "Wrong",
            "last_name": "Name",
            "email": "client@example.com",
        },
    )
    assert fresh.status_code == reused.status_code == 201
    # Identical shape, link-derived values only — nothing patient-derived.
    expected_keys = {"host_name", "title", "start_at", "end_at", "duration_minutes"}
    assert set(fresh.json()) == set(reused.json()) == expected_keys

    unchanged = public_client.patient_repo.get(existing.id, OWNER_ID)
    assert unchanged.first_name == "Realfirst"
    assert unchanged.last_name == "Reallast"


def test_booking_off_slot_time_is_refused(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    resp = _book(public_client, "intro-call", f"{date_str}T09:07:00Z")
    assert resp.status_code == 409


# ------------------------------------------------------- real app: flag gating


def test_public_routes_absent_when_flag_off(client: Any) -> None:
    resp = client.get("/api/public/booking-links/anything")
    assert resp.status_code == 404


# ----------------------------------------------------------- management: CRUD


@pytest.fixture
def managed_client(client: Any, link_repo: InMemoryBookingLinkRepository) -> Any:
    real_app.dependency_overrides[get_link_repository] = lambda: link_repo
    return client


def _create_link_payload(slug: str = "intro-call") -> dict[str, Any]:
    return {
        "slug": slug,
        "host_name": "Test Therapist",
        "title": "Intro call",
        "duration_minutes": 30,
    }


def test_create_and_list_booking_links(managed_client: Any) -> None:
    created = managed_client.post("/api/booking-links", json=_create_link_payload())
    assert created.status_code == 201
    assert created.json()["slug"] == "intro-call"

    listed = managed_client.get("/api/booking-links")
    assert listed.status_code == 200
    assert listed.json()["total"] == 1


def test_reserved_and_malformed_slugs_are_rejected(managed_client: Any) -> None:
    reserved = managed_client.post("/api/booking-links", json=_create_link_payload(slug="admin"))
    assert reserved.status_code == 400
    malformed = managed_client.post(
        "/api/booking-links", json=_create_link_payload(slug="Bad Slug!")
    )
    assert malformed.status_code == 422


def test_duplicate_slug_conflicts(managed_client: Any) -> None:
    assert managed_client.post("/api/booking-links", json=_create_link_payload()).status_code == 201
    dup = managed_client.post("/api/booking-links", json=_create_link_payload())
    assert dup.status_code == 409


def test_deactivate_and_delete_booking_link(
    managed_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_id = managed_client.post("/api/booking-links", json=_create_link_payload()).json()["id"]

    patched = managed_client.patch(f"/api/booking-links/{link_id}", json={"is_active": False})
    assert patched.status_code == 200
    assert patched.json()["is_active"] is False
    stored = link_repo.get_by_slug("intro-call")
    assert stored is not None
    assert stored.is_active is False

    deleted = managed_client.delete(f"/api/booking-links/{link_id}")
    assert deleted.status_code == 204
    assert link_repo.get_by_slug("intro-call") is None

    deleted_again = managed_client.delete(f"/api/booking-links/{link_id}")
    assert deleted_again.status_code == 404


# ------------------------------------------- public: owner subscription gate


class _FakeSaasSettings:
    """Just enough Settings surface for the owner subscription gate."""

    is_saas = True


def _stub_subscription_module(monkeypatch: pytest.MonkeyPatch, sub: dict[str, Any] | None) -> None:
    """Stand in for the SaaS-overlay-only ``app.routes.subscription``.

    The gate imports it lazily and only under ``is_saas``, so OSS never
    loads it; the stub lets the enforcing branch be exercised here.
    """
    module = types.ModuleType("app.routes.subscription")
    module._fetch_subscription = lambda _email, _settings: sub  # type: ignore[attr-defined]  # stub module, no stub-file to declare against
    monkeypatch.setitem(sys.modules, "app.routes.subscription", module)
    monkeypatch.setattr(public_booking_module, "get_settings", _FakeSaasSettings)


def test_booking_refused_when_owner_may_not_write(
    public_client: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wound-down practice stops accumulating charts through its link."""
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))
    link_repo.create(_link())
    slot = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()[
        "slots"
    ][0]

    _stub_subscription_module(monkeypatch, {"access_level": "read_only"})

    resp = _book(public_client, "intro-call", slot["start"])
    assert resp.status_code == 403
    # Refused before any write: no chart, no appointment, no audit entry.
    assert public_client.patient_repo.find_by_email("jane@example.com", OWNER_ID) is None
    assert public_client.audit.calls == []


def test_wound_down_practice_still_serves_card_and_slots(
    public_client: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Read-intent stays open, matching how READ_ONLY behaves elsewhere."""
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))
    link_repo.create(_link())

    _stub_subscription_module(monkeypatch, {"access_level": "read_only"})

    assert public_client.get("/api/public/booking-links/intro-call").status_code == 200
    assert (
        public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").status_code
        == 200
    )


def test_booking_allowed_while_subscription_is_still_provisioning(
    public_client: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No subscription record is mid-provisioning, not lapsed."""
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))
    link_repo.create(_link())
    slot = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()[
        "slots"
    ][0]

    _stub_subscription_module(monkeypatch, None)

    assert _book(public_client, "intro-call", slot["start"]).status_code == 201


# ------------------------------------------------- management: owner scoping


def test_another_users_link_is_invisible_to_the_caller(
    managed_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    """Every owner-facing route scopes by user_id, so a foreign id 404s."""
    foreign = link_repo.create(_link(slug="someone-else", user_id="other-user-999"))

    assert managed_client.get("/api/booking-links").json()["total"] == 0
    hijack_attempt = managed_client.patch(
        f"/api/booking-links/{foreign.id}", json={"title": "Hijacked"}
    )
    assert hijack_attempt.status_code == 404

    delete_attempt = managed_client.delete(f"/api/booking-links/{foreign.id}")
    assert delete_attempt.status_code == 404

    # Untouched, and still resolvable on the public path by its real owner.
    stored = link_repo.get_by_slug("someone-else")
    assert stored is not None
    assert stored.title == foreign.title
    assert stored.user_id == "other-user-999"
