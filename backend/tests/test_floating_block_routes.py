# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The floating-block routes: create the rule, read the day, pin or skip a date."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.models import UserPreferences
from app.routes.scheduling import (
    get_availability_engine,
    get_availability_rule_repository,
    get_scheduling_service,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import InMemoryAvailabilityRuleRepository
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService

if TYPE_CHECKING:
    from app.repositories import InMemoryUserRepository
    from fastapi.testclient import TestClient

# Far enough ahead that "a past date can't be changed" never trips.
WEDNESDAY = "2030-04-17"
LUNCH: dict[str, Any] = {
    "duration_minutes": 60,
    "window_start": "11:30",
    "window_end": "14:00",
    "days_of_week": [0, 1, 2, 3, 4],
    "label": "Lunch",
}


@pytest.fixture
def floating_client(client: TestClient, mock_user_repo: InMemoryUserRepository) -> TestClient:
    mock_user_repo.save_preferences("test-user-123", UserPreferences(timezone="UTC"))
    rules = InMemoryAvailabilityRuleRepository()
    appts = InMemoryAppointmentRepository()
    engine = AvailabilityEngine(rules, appts)
    app.dependency_overrides[get_availability_rule_repository] = lambda: rules
    app.dependency_overrides[get_availability_engine] = lambda: engine
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appts, engine)
    return client


def _create(client: TestClient) -> str:
    created = client.post(
        "/api/availability/rules",
        json={"rule_type": "floating_block", "enforcement": "hard", "params": LUNCH},
    )
    assert created.status_code == 201, created.text
    rule_id: str = created.json()["id"]
    return rule_id


def _day(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/availability/floating-blocks", params={"date": WEDNESDAY})
    assert response.status_code == 200, response.text
    (block,) = response.json()["blocks"]
    return dict(block)


def test_the_day_shows_where_the_block_settled(floating_client: TestClient) -> None:
    _create(floating_client)
    booked = floating_client.post(
        "/api/appointments",
        json={
            "patient_id": "patient-1",
            "title": "Session",
            "start_at": f"{WEDNESDAY}T11:30:00Z",
            "end_at": f"{WEDNESDAY}T12:15:00Z",
            "duration_minutes": 45,
        },
    )
    assert booked.status_code == 201, booked.text

    block = _day(floating_client)

    assert block["state"] == "floating"
    assert block["label"] == "Lunch"
    assert block["start"].startswith(f"{WEDNESDAY}T12:15")
    assert block["end"].startswith(f"{WEDNESDAY}T13:15")


def test_a_booking_into_the_last_free_hour_is_refused_naming_the_block(
    floating_client: TestClient,
) -> None:
    _create(floating_client)

    refused = floating_client.post(
        "/api/appointments",
        json={
            "patient_id": "patient-1",
            "title": "Session",
            "start_at": f"{WEDNESDAY}T12:00:00Z",
            "end_at": f"{WEDNESDAY}T13:30:00Z",
            "duration_minutes": 90,
        },
    )

    assert refused.status_code == 422, refused.text
    assert "Lunch" in refused.text


def test_pin_skip_and_clear_one_date(floating_client: TestClient) -> None:
    rule_id = _create(floating_client)
    url = f"/api/availability/rules/{rule_id}/exceptions/{WEDNESDAY}"

    pinned = floating_client.put(url, json={"start": "13:00"})
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["params"]["exceptions"] == {WEDNESDAY: {"start": "13:00"}}
    assert _day(floating_client)["state"] == "pinned"

    skipped = floating_client.put(url, json={"skip": True})
    assert skipped.status_code == 200, skipped.text
    assert _day(floating_client)["state"] == "skipped"

    cleared = floating_client.delete(url)
    assert cleared.status_code == 200, cleared.text
    assert "exceptions" not in cleared.json()["params"]
    assert _day(floating_client)["state"] == "floating"


@pytest.mark.parametrize(
    ("day", "body"),
    [
        ("2020-01-01", {"skip": True}),  # past
        (WEDNESDAY, {}),  # neither pin nor skip
        (WEDNESDAY, {"start": "13:00", "skip": True}),  # both
        (WEDNESDAY, {"start": "23:30"}),  # runs past midnight
    ],
)
def test_a_bad_exception_is_refused(
    floating_client: TestClient, day: str, body: dict[str, Any]
) -> None:
    rule_id = _create(floating_client)

    response = floating_client.put(f"/api/availability/rules/{rule_id}/exceptions/{day}", json=body)

    assert response.status_code == 422, response.text


def test_exceptions_only_exist_on_a_floating_block(floating_client: TestClient) -> None:
    other = floating_client.post(
        "/api/availability/rules",
        json={"rule_type": "block_day_of_week", "params": {"day_of_week": 5}},
    )
    assert other.status_code == 201, other.text

    response = floating_client.put(
        f"/api/availability/rules/{other.json()['id']}/exceptions/{WEDNESDAY}",
        json={"skip": True},
    )

    assert response.status_code == 404
