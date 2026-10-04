# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Appointment type names are unique per clinician, and the routes say so.

The database has always refused a second type with the same name
(``uq_appointment_types_user_name``); the routes used to let that refusal
escape as a 500. These tests pin the contract the settings page relies on:
a create or rename onto a taken name is a 409 with a sentence the page can
show, the clinician's types are unchanged, and the first-read seeding does
not trip over a concurrent read that seeded first.

The Postgres side (that the constraint fires and is translated, inside a
savepoint) is in ``tests_integration/database/test_appointment_type_name_taken_db.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.main import app
from app.routes.scheduling import (
    APPOINTMENT_TYPE_NAME_TAKEN,
    _ensure_default_appointment_types,
    get_appointment_type_repository,
)
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment_type import (
    InMemoryAppointmentTypeRepository,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

USER_ID = "test-user-123"


@pytest.fixture
def type_repo(client: TestClient) -> Iterator[InMemoryAppointmentTypeRepository]:
    repo = InMemoryAppointmentTypeRepository()
    app.dependency_overrides[get_appointment_type_repository] = lambda: repo
    yield repo
    app.dependency_overrides.pop(get_appointment_type_repository, None)


def _names(repo: InMemoryAppointmentTypeRepository, user_id: str = USER_ID) -> list[str]:
    return sorted(t.name for t in repo.list_by_user(user_id))


def test_creating_a_type_with_a_taken_name_is_a_409(
    client: TestClient, type_repo: InMemoryAppointmentTypeRepository
) -> None:
    assert client.post("/api/appointment-types", json={"name": "Consultation"}).status_code == 201

    response = client.post("/api/appointment-types", json={"name": "Consultation"})

    assert response.status_code == 409, response.text
    assert APPOINTMENT_TYPE_NAME_TAKEN in response.text
    assert _names(type_repo) == ["Consultation"]


def test_renaming_onto_a_taken_name_is_a_409(
    client: TestClient, type_repo: InMemoryAppointmentTypeRepository
) -> None:
    # Only the status and sentence are checked here. The in-memory repository
    # hands back its stored instance, which the route edits in place before
    # saving, so it cannot show the old name surviving; the Postgres test
    # proves that, because there the refused write is rolled back.
    client.post("/api/appointment-types", json={"name": "Consultation"})
    intake = client.post("/api/appointment-types", json={"name": "Intake"}).json()

    response = client.patch(f"/api/appointment-types/{intake['id']}", json={"name": "Consultation"})

    assert response.status_code == 409, response.text
    assert APPOINTMENT_TYPE_NAME_TAKEN in response.text


def test_saving_a_type_under_its_own_name_is_not_a_collision(
    client: TestClient, type_repo: InMemoryAppointmentTypeRepository
) -> None:
    created = client.post("/api/appointment-types", json={"name": "Intake"}).json()

    response = client.patch(
        f"/api/appointment-types/{created['id']}",
        json={"name": "Intake", "duration_minutes": 60},
    )

    assert response.status_code == 200, response.text
    assert response.json()["duration_minutes"] == 60


def test_another_clinician_may_use_the_same_name(
    client: TestClient, type_repo: InMemoryAppointmentTypeRepository
) -> None:
    type_repo.create(AppointmentType(id="other", user_id="someone-else", name="Consultation"))

    response = client.post("/api/appointment-types", json={"name": "Consultation"})

    assert response.status_code == 201, response.text


class _RacedRepository(InMemoryAppointmentTypeRepository):
    """A repository whose first list misses rows a concurrent read just seeded."""

    def __init__(self) -> None:
        super().__init__()
        self._missed_once = False

    def list_by_user(self, user_id: str) -> list[AppointmentType]:
        if not self._missed_once:
            self._missed_once = True
            return []
        return super().list_by_user(user_id)


def test_seeding_skips_a_type_a_concurrent_first_read_already_wrote() -> None:
    repo = _RacedRepository()
    repo.create(AppointmentType(id="raced", user_id=USER_ID, name="Session"))

    types, migrated = _ensure_default_appointment_types(repo, USER_ID)

    assert sorted(t.name for t in types) == ["Consultation", "Intake", "Session"]
    # The row the faster read wrote is the one that stays.
    assert next(t.id for t in types if t.name == "Session") == "raced"
    assert migrated is False
