# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Single-slot booking links: one window, booked once.

Mounted the same way as ``test_public_booking_routes.py`` — the public router on
a bare app over in-memory repositories — and reusing its harness, so the two
kinds of link are tested against the same fakes. That two simultaneous uses
produce one booking is a property of the database row lock, and is proven
against Postgres in ``tests_integration/database/test_single_slot_link_db.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.main import app as real_app
from app.models import Patient
from app.repositories.booking_link import InMemoryBookingLinkRepository
from app.routes.booking_links import (
    get_link_repository,
    get_link_scheduling_policy,
    get_link_type_repository,
)
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment_type import (
    InMemoryAppointmentTypeRepository,
)
from app.utcnow import utc_now

from .test_public_booking_routes import (
    OWNER_ID,
    TYPE_ID,
    _appointment_type,
    _book,
    _link,
    _open_policy,
    _public_app,
)

SLUG = "slot-0123456789abcdef"


@pytest.fixture
def link_repo() -> InMemoryBookingLinkRepository:
    return InMemoryBookingLinkRepository()


@pytest.fixture
def managed_client(client: Any, link_repo: InMemoryBookingLinkRepository) -> Any:
    """The real app's management routes over in-memory links and types."""
    type_repo = InMemoryAppointmentTypeRepository()
    type_repo.create(_appointment_type())
    policy = _open_policy()
    real_app.dependency_overrides[get_link_repository] = lambda: link_repo
    real_app.dependency_overrides[get_link_type_repository] = lambda: type_repo
    real_app.dependency_overrides[get_link_scheduling_policy] = lambda: policy
    return client


def _slot_start() -> datetime:
    """A week out, on the hour: inside every window, never in the past."""
    return (utc_now() + timedelta(days=7)).replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slot_link(link_repo: InMemoryBookingLinkRepository, start: datetime) -> None:
    link = _link(slug=SLUG)
    link.slot_start_at = start
    link.slot_end_at = start + timedelta(minutes=30)
    link_repo.create(link)


def _live(client: Any) -> list[Appointment]:
    return [
        a
        for a in client.appt_repo._appointments.values()
        if a.status != AppointmentStatus.CANCELLED
    ]


# ------------------------------------------------------------------ the card


def test_the_card_offers_the_one_time(link_repo: InMemoryBookingLinkRepository) -> None:
    client = _public_app(link_repo)
    start = _slot_start()
    _slot_link(link_repo, start)

    slot = client.get(f"/api/public/booking-links/{SLUG}").json()["slot"]

    assert slot == {
        "start": _iso(start),
        "end": _iso(start + timedelta(minutes=30)),
        "taken": False,
    }


def test_the_slots_list_shows_only_the_links_time(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    client = _public_app(link_repo)
    start = _slot_start()
    _slot_link(link_repo, start)

    own_day = client.get(f"/api/public/booking-links/{SLUG}/slots?date={start.date()}").json()
    other_day = client.get(
        f"/api/public/booking-links/{SLUG}/slots?date={start.date() + timedelta(days=1)}"
    ).json()

    assert [s["start"] for s in own_day["slots"]] == [_iso(start)]
    assert other_day["slots"] == []


# ---------------------------------------------------------- self_book_mode


def test_under_request_mode_the_link_holds_the_time_pending(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    client = _public_app(link_repo)
    assert client.policy["self_book_mode"] == "request"
    start = _slot_start()
    _slot_link(link_repo, start)

    resp = _book(client, SLUG, _iso(start))

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "requested"
    [appt] = _live(client)
    assert appt.status == AppointmentStatus.PENDING
    assert appt.start_at == start
    assert appt.appointment_type_id == TYPE_ID
    assert appt.pending_expires_at is not None
    assert appt.pending_expires_at <= start
    # Nothing is mailed from a single-slot booking, even on a link that would
    # otherwise ask the booker to confirm by email.
    assert client.email.sent == []


def test_under_auto_mode_the_link_books_it(link_repo: InMemoryBookingLinkRepository) -> None:
    client = _public_app(link_repo)
    client.policy["self_book_mode"] = "auto"
    start = _slot_start()
    _slot_link(link_repo, start)

    resp = _book(client, SLUG, _iso(start))

    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "confirmed"
    [appt] = _live(client)
    assert appt.status == AppointmentStatus.CONFIRMED
    assert appt.pending_expires_at is None
    assert client.email.sent == []


# --------------------------------------------------------------- used once


def test_a_second_use_is_told_the_time_is_taken(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    client = _public_app(link_repo)
    start = _slot_start()
    _slot_link(link_repo, start)

    assert _book(client, SLUG, _iso(start)).status_code == 201
    second = _book(client, SLUG, _iso(start), email="sam@example.com")

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SLOT_TAKEN"
    assert len(_live(client)) == 1
    # The second visitor left nothing behind.
    assert client.patient_repo.find_by_email("sam@example.com", OWNER_ID) is None
    # And the card says so before anyone tries.
    assert client.get(f"/api/public/booking-links/{SLUG}").json()["slot"]["taken"] is True


def test_a_time_the_diary_already_filled_is_taken(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    client = _public_app(link_repo)
    start = _slot_start()
    _slot_link(link_repo, start)
    client.appt_repo.create(
        Appointment(
            id="booked-directly",
            user_id=OWNER_ID,
            patient_id="someone-else",
            title="Session",
            start_at=start,
            end_at=start + timedelta(minutes=50),
            duration_minutes=50,
            status=AppointmentStatus.CONFIRMED,
            session_type="Therapy session",
        )
    )

    resp = _book(client, SLUG, _iso(start))

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SLOT_TAKEN"
    assert client.patient_repo.find_by_email("jane@example.com", OWNER_ID) is None


def test_the_link_books_only_its_own_time(link_repo: InMemoryBookingLinkRepository) -> None:
    client = _public_app(link_repo)
    start = _slot_start()
    _slot_link(link_repo, start)

    resp = _book(client, SLUG, _iso(start + timedelta(hours=1)))

    assert resp.status_code == 400
    assert _live(client) == []
    # Refused before the claim, so the link is still good for its own time.
    assert _book(client, SLUG, _iso(start)).status_code == 201


def test_a_matched_email_books_against_a_placeholder_not_the_chart(
    link_repo: InMemoryBookingLinkRepository,
) -> None:
    """An unverified email never attaches a booking to an existing chart."""
    client = _public_app(link_repo)
    client.policy["self_book_mode"] = "auto"
    start = _slot_start()
    _slot_link(link_repo, start)
    chart = client.patient_repo.create(
        Patient(
            id="real-chart",
            first_name="Jane",
            last_name="Roe",
            email="jane@example.com",
            created_at=utc_now(),
            updated_at=utc_now(),
        ),
        OWNER_ID,
    )

    assert _book(client, SLUG, _iso(start)).status_code == 201

    [appt] = _live(client)
    assert appt.patient_id != chart.id


# ------------------------------------------------------------- owner side


def test_an_owner_creates_a_single_slot_link(managed_client: Any) -> None:
    start = _slot_start()

    resp = managed_client.post(
        "/api/booking-links/slot",
        json={
            "appointment_type_id": TYPE_ID,
            "start_at": start.isoformat(),
            "host_name": "Test Therapist",
            "title": "Earlier time",
        },
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slug"].startswith("slot-")
    assert datetime.fromisoformat(body["slot_start_at"]) == start
    assert datetime.fromisoformat(body["slot_end_at"]) == start + timedelta(minutes=30)
    assert body["slot_claimed_at"] is None
    assert body["bookable"] is True


def test_a_single_slot_link_needs_a_future_time_with_an_offset(managed_client: Any) -> None:
    payload = {"appointment_type_id": TYPE_ID, "host_name": "Test Therapist", "title": "Earlier"}
    past = (utc_now() - timedelta(hours=1)).isoformat()
    naive = _slot_start().replace(tzinfo=None).isoformat()

    assert (
        managed_client.post("/api/booking-links/slot", json={**payload, "start_at": past})
    ).status_code == 400
    assert (
        managed_client.post("/api/booking-links/slot", json={**payload, "start_at": naive})
    ).status_code == 400
