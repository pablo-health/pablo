# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for public booking links: owner-facing CRUD and the public surface.

The public router is flag-gated in the real app (PUBLIC_BOOKING_ENABLED,
default off), so — same as the launch-intent tests — the public tests
mount the router on a bare FastAPI app and override its dependencies
with in-memory repositories. The management routes are always mounted
and use the standard ``client`` fixture.
"""

from __future__ import annotations

import hashlib
import re
import secrets
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
from app.models.audit import ACTOR_TYPE_ANONYMOUS, ACTOR_TYPE_CLINICIAN, AuditAction
from app.models.booking_link import (
    BookingLink,
    BookingLinkResponse,
    CreateBookingLinkRequest,
    PublicBookingLinkResponse,
    UpdateBookingLinkRequest,
)
from app.rate_limit import (
    require_public_booking_rate_limit,
    require_public_booking_write_rate_limit,
)
from app.repositories import (
    get_booking_link_repository,
    get_patient_repository,
    get_user_repository,
)
from app.repositories.audit import InMemoryAuditRepository
from app.repositories.booking_link import InMemoryBookingLinkRepository, SlugTakenError
from app.repositories.patient import InMemoryPatientRepository
from app.routes import public_booking as public_booking_module
from app.routes.booking_links import get_link_repository
from app.routes.public_booking import (
    get_public_appointment_repository,
    get_public_availability_engine,
    get_public_gcal_service,
    get_public_scheduling_service,
)
from app.routes.public_booking import (
    router as public_router,
)
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.models.availability import AvailabilityRule, RuleType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services import get_audit_service
from app.services.audit_service import AuditService
from app.services.email_sender import (
    EmailSender,
    InMemoryEmailSender,
    NoneEmailSender,
    get_email_sender,
)
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
    require_email_confirmation: bool = True,
    practice_edition: str | None = None,
    practice_is_active: bool | None = None,
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
        require_email_confirmation=require_email_confirmation,
        practice_edition=practice_edition,
        practice_is_active=practice_is_active,
    )


class _FailingEmailSender:
    """Test double whose ``send`` always raises, for the cleanup-on-failure path."""

    can_deliver = True

    def send(self, message: Any) -> None:
        raise RuntimeError("smtp down")


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
        self,
        action: Any,
        user: Any,
        request: Any,
        patient: Any,
        changes: Any = None,
        actor_type: str = ACTOR_TYPE_CLINICIAN,
    ) -> None:
        self.calls.append(
            {
                "action": action,
                "patient": patient,
                "changes": changes,
                "actor_type": actor_type,
            }
        )

    def log_appointment_action(
        self,
        action: Any,
        user: Any,
        request: Any,
        appointment_id: str,
        patient_id: str | None = None,
        changes: Any = None,
        actor_type: str = ACTOR_TYPE_CLINICIAN,
    ) -> None:
        self.calls.append(
            {
                "action": action,
                "appointment_id": appointment_id,
                "patient_id": patient_id,
                "changes": changes,
                "actor_type": actor_type,
            }
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


def _public_app(
    link_repo: InMemoryBookingLinkRepository,
    audit: Any = None,
    email_sender: EmailSender | None = None,
) -> Any:
    """A TestClient over an app that mounts only the public router.

    ``audit`` defaults to the recording fake; pass a real ``AuditService``
    to assert on the rows that actually land in a repository. ``email_sender``
    defaults to an ``InMemoryEmailSender`` that can always deliver; pass a
    ``NoneEmailSender`` or a failing double to exercise the refuse-to-arm and
    cleanup-on-failure paths.
    """
    appt_repo = InMemoryAppointmentRepository()
    rule_repo = InMemoryAvailabilityRuleRepository()
    patient_repo = InMemoryPatientRepository()
    fake_audit = audit if audit is not None else _FakeAudit()
    fake_email = email_sender if email_sender is not None else InMemoryEmailSender()

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
    app.dependency_overrides[get_public_appointment_repository] = lambda: appt_repo
    app.dependency_overrides[get_patient_repository] = lambda: patient_repo
    app.dependency_overrides[get_public_gcal_service] = lambda: gcal
    app.dependency_overrides[get_audit_service] = lambda: fake_audit
    app.dependency_overrides[get_email_sender] = lambda: fake_email

    client = TestClient(app)
    client.rule_repo = rule_repo  # type: ignore[attr-defined]  # test-only stash, keeps fixtures to one object
    client.patient_repo = patient_repo  # type: ignore[attr-defined]  # test-only stash
    client.appt_repo = appt_repo  # type: ignore[attr-defined]  # test-only stash
    client.audit = fake_audit  # type: ignore[attr-defined]  # test-only stash
    client.email = fake_email  # type: ignore[attr-defined]  # test-only stash
    client.gcal = gcal  # type: ignore[attr-defined]  # test-only stash
    return client


@pytest.fixture
def public_client(link_repo: InMemoryBookingLinkRepository) -> Any:
    return _public_app(link_repo)


def _book(
    client: Any,
    slug: str,
    start_at: str,
    email: str = "jane@example.com",
    note: str | None = None,
) -> Any:
    payload = {
        "start_at": start_at,
        "first_name": "Jane",
        "last_name": "Roe",
        "email": email,
    }
    if note is not None:
        payload["note"] = note
    return client.post(f"/api/public/booking-links/{slug}/bookings", json=payload)


def _hold(
    client: Any, slug: str, start_at: str, email: str = "jane@example.com"
) -> tuple[Any, str]:
    """Book a slot on a default (born-true) link and return (response, token).

    The token is parsed out of the confirmation email the hold sends —
    the same thing a real booker would click through from their inbox.
    """
    resp = _book(client, slug, start_at, email=email)
    match = re.search(r"token=(\S+)", client.email.sent[-1].text)
    assert match is not None
    return resp, match.group(1)


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
    """A disabled owner or a wound-down practice refuse the same as a missing
    or a deactivated slug — the same 404, on the card, the slots and the
    booking POST alike."""
    disabled_owner_id = "disabled-owner-789"
    disabled_owner = User(
        id=disabled_owner_id,
        email="disabled@example.com",
        name="Disabled Therapist",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
        status="disabled",
    )
    public_client.app.dependency_overrides[get_user_repository] = lambda: _FakeUserRepo(
        {OWNER_ID: _owner(), disabled_owner_id: disabled_owner}
    )
    link_repo.create(_link(slug="paused-link", is_active=False))
    link_repo.create(_link(slug="disabled-owner-link", user_id=disabled_owner_id))
    link_repo.create(_link(slug="wound-down-practice-link", practice_is_active=False))

    date_str = _bookable_date()
    slugs = [
        "no-such-link",
        "paused-link",
        "disabled-owner-link",
        "wound-down-practice-link",
    ]

    cards = [public_client.get(f"/api/public/booking-links/{slug}") for slug in slugs]
    slots = [
        public_client.get(f"/api/public/booking-links/{slug}/slots?date={date_str}")
        for slug in slugs
    ]
    bookings = [_book(public_client, slug, f"{date_str}T09:30:00Z") for slug in slugs]

    for responses in (cards, slots, bookings):
        assert all(r.status_code == 404 for r in responses)
        assert all(r.json() == responses[0].json() for r in responses)

    # The disabled-owner refusal happens before any write.
    assert public_client.patient_repo.list_by_user(disabled_owner_id)[1] == 0
    assert public_client.audit.calls == []


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
    link_repo.create(_link(require_email_confirmation=False))
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

    # Nobody signed in to make this booking; the trail must not read as though
    # the therapist created the chart and the appointment themselves.
    link = link_repo.get_by_slug("intro-call")
    assert link is not None
    for call in public_client.audit.calls:
        assert call["actor_type"] == ACTOR_TYPE_ANONYMOUS
        assert call["changes"]["source"] == "public_booking"
        assert call["changes"]["booking_link_id"] == link.id
        assert call["changes"]["booking_link_slug"] == "intro-call"
        # Provenance is ids and slugs. The booker's own details stay out.
        assert "jane@example.com" not in call["changes"].values()
        assert "Jane" not in call["changes"].values()
        assert "Roe" not in call["changes"].values()


def test_booked_slot_is_no_longer_offered_or_bookable(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link(require_email_confirmation=False))
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
    """Attach-by-match survives only on a personal-edition practice — see
    test_relaxed_link_never_attaches_unverified_email_to_existing_chart for
    the default (therapist) practice, where a repeat booker instead lands
    as a fresh quarantined placeholder every time."""
    link_repo.create(_link(require_email_confirmation=False, practice_edition="personal"))
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    assert _book(public_client, "intro-call", f"{date_str}T09:00:00Z").status_code == 201
    assert _book(public_client, "intro-call", f"{date_str}T10:00:00Z").status_code == 201

    _patients, total = public_client.patient_repo.list_by_user(OWNER_ID)
    assert total == 1

    # The reuse path skips patient_created, so the only rows are the two
    # appointments — and they are anonymous too.
    actions = [str(c["action"]) for c in public_client.audit.calls]
    assert not any("patient_created" in a for a in actions[1:])
    assert [c["actor_type"] for c in public_client.audit.calls] == [ACTOR_TYPE_ANONYMOUS] * len(
        public_client.audit.calls
    )


def test_public_booking_audit_rows_live_in_the_owner_scope_as_anonymous(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    """The whole point of the discriminator, end to end.

    The rows still belong to the owner — they are written under the owner's
    RLS context and the owner is who reads them back — but they say an
    anonymous principal acted, and the request context carries the only
    identity the booker has.
    """
    repo = InMemoryAuditRepository()
    client = _public_app(link_repo, audit=AuditService(repo))
    link_repo.create(_link(require_email_confirmation=False))
    date_str = _bookable_date()
    client.rule_repo.create(_working_hours_rule(date_str))

    resp = client.post(
        "/api/public/booking-links/intro-call/bookings",
        json={
            "start_at": f"{date_str}T09:30:00Z",
            "first_name": "Jane",
            "last_name": "Roe",
            "email": "jane@example.com",
        },
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    assert resp.status_code == 201

    rows = repo.list_for_user(OWNER_ID)
    assert {r.action for r in rows} == {
        AuditAction.PATIENT_CREATED.value,
        AuditAction.APPOINTMENT_CREATED.value,
    }
    assert len(rows) == 2
    for row in rows:
        assert row.actor_type == ACTOR_TYPE_ANONYMOUS
        assert row.user_id == OWNER_ID
        assert row.ip_address == "203.0.113.9"
        assert row.patient_id is not None


def test_booking_reveals_nothing_about_existing_patients(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    """No existence oracle: booking with an existing patient's email must be
    indistinguishable from booking with a fresh one — the confirmation carries
    only link-derived fields, and the existing chart is never modified by
    attacker-supplied names."""
    link_repo.create(_link(require_email_confirmation=False))
    link_repo.create(_link(slug="hold-link"))
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
    expected_keys = {"host_name", "title", "start_at", "end_at", "duration_minutes", "status"}
    assert set(fresh.json()) == set(reused.json()) == expected_keys
    assert fresh.json()["status"] == reused.json()["status"] == "confirmed"

    # Same no-oracle property on a born-true link: both hold, same shape.
    fresh_hold = public_client.post(
        "/api/public/booking-links/hold-link/bookings",
        json={
            "start_at": f"{date_str}T10:00:00Z",
            "first_name": "New",
            "last_name": "Person",
            "email": "other-stranger@example.com",
        },
    )
    reused_hold = public_client.post(
        "/api/public/booking-links/hold-link/bookings",
        json={
            "start_at": f"{date_str}T10:30:00Z",
            "first_name": "Wrong",
            "last_name": "Name",
            "email": "client@example.com",
        },
    )
    assert fresh_hold.status_code == reused_hold.status_code == 201
    assert set(fresh_hold.json()) == set(reused_hold.json()) == expected_keys
    assert fresh_hold.json()["status"] == reused_hold.json()["status"] == "pending_confirmation"

    unchanged = public_client.patient_repo.get(existing.id, OWNER_ID)
    assert unchanged.first_name == "Realfirst"
    assert unchanged.last_name == "Reallast"


def test_booking_off_slot_time_is_refused(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link(require_email_confirmation=False))
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    resp = _book(public_client, "intro-call", f"{date_str}T09:07:00Z")
    assert resp.status_code == 409


# ------------------------------------------------ public: email-confirmation hold


def test_required_link_places_a_hold_and_emails_a_confirmation(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    resp = _book(public_client, "intro-call", f"{date_str}T09:30:00Z", note="Please call ahead")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending_confirmation"
    assert set(body) == {"host_name", "title", "start_at", "end_at", "duration_minutes", "status"}

    appts = list(public_client.appt_repo._appointments.values())
    assert len(appts) == 1
    appt = appts[0]
    assert appt.status == "pending"
    assert appt.pending_expires_at is not None
    expected_expiry = utc_now() + timedelta(minutes=15)
    assert abs((appt.pending_expires_at - expected_expiry).total_seconds()) < 60
    assert appt.confirmation_token_hash is not None
    assert len(appt.confirmation_token_hash) == 64
    int(appt.confirmation_token_hash, 16)  # a valid hex digest

    assert len(public_client.email.sent) == 1
    message = public_client.email.sent[0]
    assert message.kind == "booking_confirmation"
    assert message.to == "jane@example.com"
    assert "/book/intro-call/confirm?token=" in message.text
    token = re.search(r"token=(\S+)", message.text).group(1)  # type: ignore[union-attr]  # match is guaranteed by the assert above
    assert hashlib.sha256(token.encode()).hexdigest() == appt.confirmation_token_hash
    assert "Please call ahead" not in message.text

    patient = public_client.patient_repo.get(appt.patient_id, OWNER_ID)
    assert patient is not None
    assert patient.status == "pending"
    assert patient.origin == "public_booking"

    slots = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()
    assert f"{date_str}T09:30:00Z" not in [s["start"] for s in slots["slots"]]

    public_client.gcal.push_appointment.assert_not_called()


def test_hold_never_attaches_to_an_existing_chart(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
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

    resp = _book(public_client, "intro-call", f"{date_str}T09:30:00Z", email="client@example.com")
    assert resp.status_code == 201

    appts = list(public_client.appt_repo._appointments.values())
    assert len(appts) == 1
    assert appts[0].patient_id != existing.id

    unchanged = public_client.patient_repo.get(existing.id, OWNER_ID)
    assert unchanged.first_name == "Realfirst"
    assert unchanged.last_name == "Reallast"

    _patients, total = public_client.patient_repo.list_by_user(OWNER_ID)
    assert total == 1  # the placeholder is quarantined out of the list

    found = public_client.patient_repo.find_by_email("client@example.com", OWNER_ID)
    assert found is not None
    assert found.id == existing.id


def test_required_link_refuses_when_email_cannot_deliver(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    client = _public_app(link_repo, email_sender=NoneEmailSender())
    date_str = _bookable_date()
    client.rule_repo.create(_working_hours_rule(date_str))
    link_repo.create(_link())
    link_repo.create(_link(slug="relaxed-link", require_email_confirmation=False))

    resp = _book(client, "intro-call", f"{date_str}T09:30:00Z")
    assert resp.status_code == 403
    assert resp.json()["error"]["message"] == public_booking_module._BOOKING_CLOSED
    assert client.patient_repo.list_by_user(OWNER_ID)[1] == 0
    assert list(client.appt_repo._appointments.values()) == []
    assert client.audit.calls == []

    # A relaxed link needs no delivery and still books.
    relaxed = _book(client, "relaxed-link", f"{date_str}T09:30:00Z")
    assert relaxed.status_code == 201


def test_send_failure_releases_the_hold(link_repo: InMemoryBookingLinkRepository) -> None:
    client = _public_app(link_repo, email_sender=_FailingEmailSender())
    date_str = _bookable_date()
    client.rule_repo.create(_working_hours_rule(date_str))
    link_repo.create(_link())

    resp = _book(client, "intro-call", f"{date_str}T09:30:00Z")
    assert resp.status_code == 403

    appts = list(client.appt_repo._appointments.values())
    assert all(a.status == "cancelled" for a in appts)
    assert client.patient_repo.list_by_user(OWNER_ID)[1] == 0

    slots = client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()
    assert f"{date_str}T09:30:00Z" in [s["start"] for s in slots["slots"]]


def test_relaxed_link_books_instantly_exactly_as_before(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link(require_email_confirmation=False))
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    resp = _book(public_client, "intro-call", f"{date_str}T09:30:00Z")
    assert resp.status_code == 201
    assert resp.json()["status"] == "confirmed"

    appts = list(public_client.appt_repo._appointments.values())
    assert len(appts) == 1
    appt = appts[0]
    assert appt.status == "confirmed"
    assert appt.pending_expires_at is None
    assert appt.confirmation_token_hash is None

    assert public_client.email.sent == []
    public_client.gcal.push_appointment.assert_called_once()


def test_relaxed_link_never_attaches_unverified_email_to_existing_chart(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    """The invariant with the flag off: a matched email on a relaxed,
    non-personal link books a fresh quarantined placeholder, never the
    chart it matched."""
    link_repo.create(_link(require_email_confirmation=False))
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

    resp = public_client.post(
        "/api/public/booking-links/intro-call/bookings",
        json={
            "start_at": f"{date_str}T09:00:00Z",
            "first_name": "Wrong",
            "last_name": "Name",
            "email": "client@example.com",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["status"] == "confirmed"

    appt = next(iter(public_client.appt_repo._appointments.values()))
    assert appt.patient_id != existing.id

    placeholder = public_client.patient_repo.get(appt.patient_id, OWNER_ID)
    assert placeholder is not None
    assert placeholder.status == "pending"
    assert placeholder.origin == "public_booking"
    assert placeholder.first_name == "Wrong"

    unchanged = public_client.patient_repo.get(existing.id, OWNER_ID)
    assert unchanged.first_name == "Realfirst"
    assert unchanged.last_name == "Reallast"

    _patients, total = public_client.patient_repo.list_by_user(OWNER_ID)
    assert total == 1  # the placeholder is quarantined out of the list

    created = [
        c
        for c in public_client.audit.calls
        if c["action"] == AuditAction.PATIENT_CREATED and c["patient"].id == placeholder.id
    ]
    assert len(created) == 1
    assert created[0]["changes"]["reason"] == "unverified_email_matched_chart"

    slots = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()
    assert f"{date_str}T09:00:00Z" not in [s["start"] for s in slots["slots"]]


def test_relaxed_link_fresh_email_creates_active_patient_with_origin(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link(require_email_confirmation=False))
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    resp = _book(public_client, "intro-call", f"{date_str}T09:00:00Z", email="fresh@example.com")
    assert resp.status_code == 201

    patient = public_client.patient_repo.find_by_email("fresh@example.com", OWNER_ID)
    assert patient is not None
    assert patient.status == "active"
    assert patient.origin == "public_booking"

    _patients, total = public_client.patient_repo.list_by_user(OWNER_ID)
    assert total == 1


def test_personal_edition_practice_may_attach_by_match(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link(require_email_confirmation=False, practice_edition="personal"))
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

    resp = public_client.post(
        "/api/public/booking-links/intro-call/bookings",
        json={
            "start_at": f"{date_str}T09:00:00Z",
            "first_name": "Wrong",
            "last_name": "Name",
            "email": "client@example.com",
        },
    )
    assert resp.status_code == 201

    appt = next(iter(public_client.appt_repo._appointments.values()))
    assert appt.patient_id == existing.id

    _patients, total = public_client.patient_repo.list_by_user(OWNER_ID)
    assert total == 1
    assert not any(c["action"] == AuditAction.PATIENT_CREATED for c in public_client.audit.calls)

    unchanged = public_client.patient_repo.get(existing.id, OWNER_ID)
    assert unchanged.first_name == "Realfirst"
    assert unchanged.last_name == "Reallast"


@pytest.mark.parametrize("practice_edition", [None, "therapist"])
def test_carve_out_is_keyed_to_edition_not_a_toggle(
    public_client: Any,
    link_repo: InMemoryBookingLinkRepository,
    practice_edition: str | None,
) -> None:
    link_repo.create(_link(require_email_confirmation=False, practice_edition=practice_edition))
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

    resp = public_client.post(
        "/api/public/booking-links/intro-call/bookings",
        json={
            "start_at": f"{date_str}T09:00:00Z",
            "first_name": "Wrong",
            "last_name": "Name",
            "email": "client@example.com",
        },
    )
    assert resp.status_code == 201

    appt = next(iter(public_client.appt_repo._appointments.values()))
    assert appt.patient_id != existing.id

    # There is no request field, settings field, or booking-link column
    # that changes this outcome — only the practice's declared edition.
    assert "practice_edition" not in CreateBookingLinkRequest.model_fields
    assert "practice_edition" not in UpdateBookingLinkRequest.model_fields
    assert "practice_edition" not in BookingLinkResponse.model_fields
    assert "practice_edition" not in PublicBookingLinkResponse.model_fields


def test_get_by_slug_carries_practice_edition(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    link_repo.create(_link(practice_edition="personal"))
    resolved = link_repo.get_by_slug("intro-call")
    assert resolved is not None
    assert resolved.practice_edition == "personal"


@pytest.mark.parametrize("practice_is_active", [True, False, None])
def test_get_by_slug_carries_practice_is_active(
    link_repo: InMemoryBookingLinkRepository,
    practice_is_active: bool | None,
) -> None:
    """None means no practice row (single-tenant); True/False mirror the
    owning practice's active state, as PostgresBookingLinkRepository.get_by_slug
    derives it from ``PracticeRow.is_active`` and ``PracticeRow.deleted_at``."""
    link_repo.create(_link(practice_is_active=practice_is_active))
    resolved = link_repo.get_by_slug("intro-call")
    assert resolved is not None
    assert resolved.practice_is_active is practice_is_active


def test_sweep_ignores_relaxed_path_placeholders(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    """A relaxed-path placeholder has no pending appointment and no
    confirmation token, so the hold-sweep dependency never touches it —
    it survives exactly like any other chart until a person reconciles
    it."""
    link_repo.create(_link(require_email_confirmation=False))
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    now = utc_now()
    public_client.patient_repo.create(
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
    resp = public_client.post(
        "/api/public/booking-links/intro-call/bookings",
        json={
            "start_at": f"{date_str}T09:00:00Z",
            "first_name": "Wrong",
            "last_name": "Name",
            "email": "client@example.com",
        },
    )
    assert resp.status_code == 201
    appt = next(iter(public_client.appt_repo._appointments.values()))
    placeholder_id = appt.patient_id

    # A slots GET runs sweep_expired_holds as a dependency; the placeholder
    # must still be there afterward.
    public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}")

    placeholder = public_client.patient_repo.get(placeholder_id, OWNER_ID)
    assert placeholder is not None
    assert placeholder.status == "pending"


# ---------------------------------------------------- public: hold confirmation


def test_hold_participates_in_buffers_max_per_day_and_free_slots(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    """A hold is just an appointment to everything downstream of it — no
    engine change was needed for it to occupy its slot, apply its buffer,
    and count toward the daily cap."""
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))
    public_client.rule_repo.create(
        AvailabilityRule(
            id=str(uuid.uuid4()),
            user_id=OWNER_ID,
            rule_type=RuleType.BUFFER_AFTER,
            enforcement="hard",
            params={"minutes": 30},
        )
    )
    public_client.rule_repo.create(
        AvailabilityRule(
            id=str(uuid.uuid4()),
            user_id=OWNER_ID,
            rule_type=RuleType.MAX_PER_DAY,
            enforcement="hard",
            params={"max": 2},
        )
    )

    resp, _token = _hold(public_client, "intro-call", f"{date_str}T09:00:00Z")
    assert resp.status_code == 201

    starts = [
        s["start"]
        for s in public_client.get(
            f"/api/public/booking-links/intro-call/slots?date={date_str}"
        ).json()["slots"]
    ]
    assert f"{date_str}T09:00:00Z" not in starts
    assert f"{date_str}T09:30:00Z" not in starts

    resp2, _token2 = _hold(
        public_client, "intro-call", f"{date_str}T10:00:00Z", email="b@example.com"
    )
    assert resp2.status_code == 201

    slots = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()
    assert slots["slots"] == []


def test_expired_hold_releases_slot_and_sweeps_placeholder(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    _resp, _token = _hold(public_client, "intro-call", f"{date_str}T09:00:00Z")
    appt = next(iter(public_client.appt_repo._appointments.values()))
    appt.pending_expires_at = utc_now() - timedelta(seconds=1)
    patient_id = appt.patient_id

    slots = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()
    assert f"{date_str}T09:00:00Z" in [s["start"] for s in slots["slots"]]

    assert appt.status == "cancelled"
    assert appt.confirmation_token_hash is not None

    assert public_client.patient_repo.get(patient_id, OWNER_ID) is None
    assert public_client.patient_repo.list_recently_deleted(OWNER_ID) == []

    deletions = [c for c in public_client.audit.calls if c["action"] == AuditAction.PATIENT_DELETED]
    assert any(c["changes"] == {"source": "public_booking_hold_expired"} for c in deletions)


def test_sweep_never_touches_a_real_chart(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    now = utc_now()
    patient = public_client.patient_repo.create(
        Patient(
            id=str(uuid.uuid4()),
            first_name="Real",
            last_name="Chart",
            email="real@example.com",
            status="active",
            created_at=now,
            updated_at=now,
        ),
        OWNER_ID,
    )
    appt = public_client.appt_repo.create(
        Appointment(
            id=str(uuid.uuid4()),
            user_id=OWNER_ID,
            patient_id=patient.id,
            title="A request from some other surface",
            start_at=datetime.fromisoformat(f"{date_str}T09:00:00+00:00"),
            end_at=datetime.fromisoformat(f"{date_str}T09:30:00+00:00"),
            duration_minutes=30,
            status="pending",
            session_type="individual",
            pending_expires_at=now - timedelta(hours=1),
            created_at=now,
            updated_at=now,
        )
    )

    public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}")

    assert appt.status == "cancelled"
    unchanged = public_client.patient_repo.get(patient.id, OWNER_ID)
    assert unchanged is not None
    assert unchanged.status == "active"


def test_confirm_finalizes_hold_and_promotes_placeholder(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    hold_resp, token = _hold(public_client, "intro-call", f"{date_str}T09:00:00Z")
    appt = next(iter(public_client.appt_repo._appointments.values()))

    resp = public_client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "confirmed"
    assert set(body) == set(hold_resp.json())

    assert appt.status == "confirmed"
    assert appt.pending_expires_at is None

    patient = public_client.patient_repo.get(appt.patient_id, OWNER_ID)
    assert patient is not None
    assert patient.status == "active"
    assert patient.origin == "public_booking"

    public_client.gcal.push_appointment.assert_called_once()
    assert any(
        c["action"] == AuditAction.APPOINTMENT_UPDATED
        and c["changes"] == {"source": "public_booking", "status": "confirmed"}
        for c in public_client.audit.calls
    )

    calls_before = len(public_client.audit.calls)
    second = public_client.post(
        "/api/public/booking-links/intro-call/confirm", json={"token": token}
    )
    assert second.status_code == 200
    assert len(public_client.audit.calls) == calls_before
    public_client.gcal.push_appointment.assert_called_once()


def test_confirm_attaches_verified_email_to_existing_chart(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
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

    _resp, token = _hold(
        public_client, "intro-call", f"{date_str}T09:00:00Z", email="client@example.com"
    )
    appt = next(iter(public_client.appt_repo._appointments.values()))
    placeholder_id = appt.patient_id
    assert placeholder_id != existing.id

    resp = public_client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 200

    assert appt.patient_id == existing.id
    assert public_client.patient_repo.get(placeholder_id, OWNER_ID) is None
    assert placeholder_id not in [
        p.id for p, _ in public_client.patient_repo.list_recently_deleted(OWNER_ID)
    ]

    unchanged = public_client.patient_repo.get(existing.id, OWNER_ID)
    assert unchanged.first_name == "Realfirst"
    assert unchanged.last_name == "Reallast"

    _patients, total = public_client.patient_repo.list_by_user(OWNER_ID)
    assert total == 1


def test_confirm_with_unknown_token_is_404(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    resp = public_client.post(
        "/api/public/booking-links/intro-call/confirm", json={"token": "not-a-real-token"}
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == public_booking_module._CONFIRMATION_INVALID


def test_clinician_cancel_kills_the_token(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    _resp, token = _hold(public_client, "intro-call", f"{date_str}T09:00:00Z")
    appt = next(iter(public_client.appt_repo._appointments.values()))

    SchedulingService(public_client.appt_repo).cancel_appointment(appt.id, OWNER_ID)
    assert appt.confirmation_token_hash is None

    resp = public_client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == public_booking_module._CONFIRMATION_INVALID
    assert appt.status == "cancelled"


def test_token_is_bound_to_its_link(link_repo: InMemoryBookingLinkRepository) -> None:
    other_owner = User(
        id="other-owner-456",
        email="other@example.com",
        name="Other Therapist",
        created_at=datetime.fromisoformat("2024-01-01T00:00:00+00:00"),
    )
    client = _public_app(link_repo)
    client.app.dependency_overrides[get_user_repository] = lambda: _FakeUserRepo(
        {OWNER_ID: _owner(), other_owner.id: other_owner}
    )
    date_str = _bookable_date()
    client.rule_repo.create(_working_hours_rule(date_str))
    link_repo.create(_link(slug="link-a", user_id=OWNER_ID))
    link_repo.create(_link(slug="link-b", user_id=other_owner.id))

    _resp, token = _hold(client, "link-a", f"{date_str}T09:00:00Z")
    appt = next(iter(client.appt_repo._appointments.values()))

    resp = client.post("/api/public/booking-links/link-b/confirm", json={"token": token})
    assert resp.status_code == 404
    assert resp.json()["error"]["message"] == public_booking_module._CONFIRMATION_INVALID
    assert appt.status == "pending"


def test_expired_hold_click_finalizes_when_slot_still_free(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    _resp, token = _hold(public_client, "intro-call", f"{date_str}T09:00:00Z")
    appt = next(iter(public_client.appt_repo._appointments.values()))
    appt.pending_expires_at = utc_now() - timedelta(seconds=1)

    public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}")
    assert appt.status == "cancelled"

    resp = public_client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
    assert appt.status == "confirmed"

    patient = public_client.patient_repo.get(appt.patient_id, OWNER_ID)
    assert patient is not None
    assert patient.status == "active"


def test_expired_hold_click_when_slot_taken_says_pick_another_time(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    link_repo.create(_link(slug="relaxed-link", require_email_confirmation=False))
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    _resp, token = _hold(public_client, "intro-call", f"{date_str}T09:00:00Z")
    appt = next(iter(public_client.appt_repo._appointments.values()))
    appt.pending_expires_at = utc_now() - timedelta(seconds=1)

    public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}")
    assert appt.status == "cancelled"

    taken = _book(public_client, "relaxed-link", f"{date_str}T09:00:00Z", email="other@example.com")
    assert taken.status_code == 201

    resp = public_client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 409
    assert resp.json()["error"]["message"] == public_booking_module._SLOT_TAKEN

    assert appt.status == "cancelled"
    assert public_client.patient_repo.get(appt.patient_id, OWNER_ID) is None


def test_two_holds_on_one_slot_cannot_both_confirm(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    start_at = datetime.fromisoformat(f"{date_str}T09:00:00+00:00")
    end_at = datetime.fromisoformat(f"{date_str}T09:30:00+00:00")
    now = utc_now()

    def _direct_hold(email: str) -> tuple[Appointment, str]:
        placeholder = public_client.patient_repo.create(
            Patient(
                id=str(uuid.uuid4()),
                first_name="Jane",
                last_name="Roe",
                email=email,
                status="pending",
                origin="public_booking",
                created_at=now,
                updated_at=now,
            ),
            OWNER_ID,
        )
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        appt = public_client.appt_repo.create(
            Appointment(
                id=str(uuid.uuid4()),
                user_id=OWNER_ID,
                patient_id=placeholder.id,
                title="Intro call",
                start_at=start_at,
                end_at=end_at,
                duration_minutes=30,
                status="pending",
                session_type="individual",
                pending_expires_at=now + timedelta(minutes=15),
                confirmation_token_hash=token_hash,
                created_at=now,
                updated_at=now,
            )
        )
        return appt, token

    appt_a, token_a = _direct_hold("a@example.com")
    appt_b, token_b = _direct_hold("b@example.com")

    resp_a = public_client.post(
        "/api/public/booking-links/intro-call/confirm", json={"token": token_a}
    )
    assert resp_a.status_code == 200

    resp_b = public_client.post(
        "/api/public/booking-links/intro-call/confirm", json={"token": token_b}
    )
    assert resp_b.status_code == 409
    assert resp_b.json()["error"]["message"] == public_booking_module._SLOT_TAKEN

    live = [a for a in public_client.appt_repo._appointments.values() if a.status != "cancelled"]
    assert len(live) == 1
    assert live[0].id == appt_a.id
    assert appt_b.status == "cancelled"
    assert public_client.patient_repo.get(appt_b.patient_id, OWNER_ID) is None

    slots = public_client.get(f"/api/public/booking-links/intro-call/slots?date={date_str}").json()
    assert f"{date_str}T09:00:00Z" not in [s["start"] for s in slots["slots"]]


def test_wound_down_practice_cannot_confirm(
    public_client: Any, link_repo: InMemoryBookingLinkRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    link_repo.create(_link())
    date_str = _bookable_date()
    public_client.rule_repo.create(_working_hours_rule(date_str))

    _resp, token = _hold(public_client, "intro-call", f"{date_str}T09:00:00Z")
    appt = next(iter(public_client.appt_repo._appointments.values()))

    _stub_subscription_module(monkeypatch, {"access_level": "read_only"})

    resp = public_client.post("/api/public/booking-links/intro-call/confirm", json={"token": token})
    assert resp.status_code == 403
    assert appt.status == "pending"


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

    reclaimed = managed_client.post("/api/booking-links", json=_create_link_payload())
    assert reclaimed.status_code == 409

    deleted_again = managed_client.delete(f"/api/booking-links/{link_id}")
    assert deleted_again.status_code == 404


def test_deleted_slug_cannot_be_reclaimed_by_another_user(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    owned = link_repo.create(_link(slug="intro-call", user_id=OWNER_ID))
    assert link_repo.delete(owned.id, OWNER_ID) is True

    with pytest.raises(SlugTakenError):
        link_repo.create(_link(slug="intro-call", user_id="other-user-999"))


def test_tombstoned_link_is_a_public_404(
    public_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link = link_repo.create(_link())
    assert link_repo.delete(link.id, OWNER_ID) is True
    unknown = public_client.get("/api/public/booking-links/no-such-link")

    card = public_client.get("/api/public/booking-links/intro-call")
    slots = public_client.get(f"/api/public/booking-links/intro-call/slots?date={_bookable_date()}")
    unknown_slots = public_client.get(
        f"/api/public/booking-links/no-such-link/slots?date={_bookable_date()}"
    )
    booking = _book(public_client, "intro-call", f"{_bookable_date()}T09:00:00Z")
    unknown_booking = _book(public_client, "no-such-link", f"{_bookable_date()}T09:00:00Z")

    assert card.status_code == 404
    assert card.json() == unknown.json()
    assert slots.status_code == 404
    assert slots.json() == unknown_slots.json()
    assert booking.status_code == 404
    assert booking.json() == unknown_booking.json()


def test_deleted_links_are_absent_from_owner_reads(
    managed_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    link_id = managed_client.post("/api/booking-links", json=_create_link_payload()).json()["id"]
    assert managed_client.delete(f"/api/booking-links/{link_id}").status_code == 204

    listed = managed_client.get("/api/booking-links")
    assert listed.json()["total"] == 0

    patched = managed_client.patch(f"/api/booking-links/{link_id}", json={"title": "New title"})
    assert patched.status_code == 404


def test_tombstone_keeps_the_row_but_flips_active(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    link = link_repo.create(_link())
    assert link_repo.delete(link.id, OWNER_ID) is True

    stored = link_repo._links[link.id]
    assert stored.deleted_at is not None
    assert stored.is_active is False
    assert link_repo.get(link.id, OWNER_ID) is None
    assert link_repo.list_by_user(OWNER_ID) == []


def test_require_email_confirmation_is_born_true_and_hidden(
    managed_client: Any, link_repo: InMemoryBookingLinkRepository
) -> None:
    created = managed_client.post("/api/booking-links", json=_create_link_payload())
    assert created.status_code == 201
    assert "require_email_confirmation" not in created.json()
    link_id = created.json()["id"]

    stored = link_repo.get_by_slug("intro-call")
    assert stored is not None
    assert stored.require_email_confirmation is True

    listed = managed_client.get("/api/booking-links")
    assert all("require_email_confirmation" not in row for row in listed.json()["data"])

    public_card = _public_app(link_repo).get("/api/public/booking-links/intro-call")
    assert "require_email_confirmation" not in public_card.json()

    patched = managed_client.patch(
        f"/api/booking-links/{link_id}", json={"require_email_confirmation": False}
    )
    assert patched.status_code == 200
    assert link_repo.get_by_slug("intro-call").require_email_confirmation is True  # type: ignore[union-attr]


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
    link_repo.create(_link(require_email_confirmation=False))
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
    link_repo.create(_link(require_email_confirmation=False))
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
