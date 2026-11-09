# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""An appointment type remembers whether it was last booked in person or by video.

A type had a name and a length and nothing about where the session happens,
so a booking made from it could not know whether it needed a video room. The
type now learns that from use: a booking that says in person (place of
service 11) or video (02, 10) writes it onto the type, and a later booking of
the type that says nothing about where takes it from there. Nobody sets it in
an editor, and a booking can always say otherwise.

These go through the route, with Zoom as the clinician's connected service,
so "gets a room" means the room was actually made.

Bug classes these cover:
  * the learned value not reaching the next booking, or reaching one that
    said where it happens and overriding it;
  * an in-person type taking the room off a booking that asked for a video
    service or pasted a link;
  * one clinician's booking teaching another clinician's type;
  * a refused booking, or an edit that is not about place, teaching the type;
  * the value settable through the type editor, which it must not be.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.routes import scheduling
from app.routes.scheduling import (
    _apply_appointment_type,
    _modality_of,
    get_appointment_type_repository,
    get_availability_rule_repository,
    get_meeting_registry,
    get_scheduling_service,
)
from app.scheduling_engine.models.appointment_type import AppointmentType
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.appointment_type import (
    InMemoryAppointmentTypeRepository,
)
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.telehealth import (
    ZOOM,
    Attendee,
    Clinician,
    MeetingLink,
    MeetingProviderRegistry,
    Practice,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

USER = "test-user-123"
OTHER = "another-clinician"
TYPE_ID = "type-session"


class Zoom:
    """A connected Zoom that makes a room every time it is asked."""

    provider_id = ZOOM
    display_name = "Zoom"

    def __init__(self) -> None:
        self.made = 0

    def is_connected(self, clinician: Clinician) -> bool:
        return True

    def create_for_appointment(
        self,
        appointment: Any,
        clinician: Clinician,
        practice: Practice,
        attendee: Attendee,
    ) -> MeetingLink:
        self.made += 1
        return MeetingLink(
            url=f"https://z.test/{self.made}", provider=ZOOM, external_id=str(self.made)
        )

    def cancel(self, external_id: str, clinician: Clinician) -> bool:
        return True


@pytest.fixture
def types() -> InMemoryAppointmentTypeRepository:
    repo = InMemoryAppointmentTypeRepository()
    repo.create(AppointmentType(id=TYPE_ID, user_id=USER, name="Session"))
    return repo


@pytest.fixture
def zoom() -> Zoom:
    return Zoom()


@pytest.fixture
def booking_client(
    client: TestClient,
    types: InMemoryAppointmentTypeRepository,
    zoom: Zoom,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    appt_repo = InMemoryAppointmentRepository()
    rule_repo = InMemoryAvailabilityRuleRepository()
    engine = AvailabilityEngine(rule_repo, appt_repo)
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo, engine)
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    app.dependency_overrides[get_appointment_type_repository] = lambda: types
    app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([zoom])
    monkeypatch.setattr(
        scheduling,
        "_clinician_view",
        lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
    )
    return client


_hour = iter(range(8, 20))


def _book(client: TestClient, **fields: Any) -> dict[str, Any]:
    """Book a fresh hour of the day, so no two bookings in a test collide."""
    hour = next(_hour)
    payload: dict[str, Any] = {
        "patient_id": "patient-1",
        "title": "Session",
        "start_at": f"2026-04-15T{hour:02d}:00:00Z",
        "end_at": f"2026-04-15T{hour:02d}:50:00Z",
        "duration_minutes": 50,
        "appointment_type_id": TYPE_ID,
    }
    payload.update(fields)
    response = client.post("/api/appointments", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _reset_hours() -> None:
    global _hour  # noqa: PLW0603
    _hour = iter(range(8, 20))


def _modality(types: InMemoryAppointmentTypeRepository, type_id: str = TYPE_ID) -> str | None:
    found = types.get(type_id, USER)
    assert found is not None
    return found.modality


class TestTheTypeLearnsFromBookings:
    def test_a_video_booking_teaches_the_type_and_the_next_one_gets_a_room(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        _book(booking_client, place_of_service="02")
        assert _modality(types) == "video"

        nothing_said = _book(booking_client)

        assert nothing_said["place_of_service"] == "02"
        assert nothing_said["provider"] == ZOOM
        assert nothing_said["video_link"] is not None

    def test_an_in_person_booking_flips_it_back_and_the_next_one_gets_no_room(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        _book(booking_client, place_of_service="10")
        _book(booking_client, place_of_service="11")
        assert _modality(types) == "in_person"

        nothing_said = _book(booking_client)

        assert nothing_said["place_of_service"] == "11"
        assert nothing_said["provider"] is None
        assert nothing_said["video_link"] is None

    def test_a_type_never_booked_reads_null_and_books_with_no_room(
        self, booking_client: TestClient, zoom: Zoom
    ) -> None:
        listed = booking_client.get("/api/appointment-types").json()["data"]
        assert [t["modality"] for t in listed if t["id"] == TYPE_ID] == [None]

        booked = _book(booking_client)

        assert booked["place_of_service"] is None
        assert booked["provider"] is None
        assert zoom.made == 0

    def test_a_booking_that_says_where_keeps_it_and_teaches_the_type(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        _book(booking_client, place_of_service="02")

        in_person = _book(booking_client, place_of_service="11")

        assert in_person["place_of_service"] == "11"
        assert in_person["provider"] is None
        assert _modality(types) == "in_person"

    def test_the_type_response_carries_what_was_learned(self, booking_client: TestClient) -> None:
        _book(booking_client, place_of_service="10")

        listed = booking_client.get("/api/appointment-types").json()["data"]

        assert [t["modality"] for t in listed if t["id"] == TYPE_ID] == ["video"]

    def test_a_refused_booking_teaches_nothing(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        _book(booking_client, place_of_service="02")

        clash = booking_client.post(
            "/api/appointments",
            json={
                "patient_id": "patient-1",
                "title": "Session",
                "start_at": "2026-04-15T08:00:00Z",
                "end_at": "2026-04-15T08:50:00Z",
                "duration_minutes": 50,
                "appointment_type_id": TYPE_ID,
                "place_of_service": "11",
            },
        )

        assert clash.status_code == 409, clash.text
        assert _modality(types) == "video"


class TestABookingThatSaysVideoAnotherWay:
    """An in-person type must not take the room off a booking asking for one."""

    def test_asking_for_a_service_is_not_overridden_by_in_person(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        _book(booking_client, place_of_service="11")

        asked = _book(booking_client, provider=ZOOM)

        assert asked["place_of_service"] is None
        assert asked["provider"] == ZOOM
        assert _modality(types) == "in_person"

    def test_a_pasted_link_is_not_overridden_by_in_person(self, booking_client: TestClient) -> None:
        _book(booking_client, place_of_service="11")

        pasted = _book(booking_client, video_link="https://practice.test/room")

        assert pasted["place_of_service"] is None
        assert pasted["video_link"] == "https://practice.test/room"


class TestEditsTeachOnlyWhenTheyMovePlace:
    def test_moving_a_visit_to_video_teaches_the_type(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        booked = _book(booking_client, place_of_service="11")

        moved = booking_client.patch(
            f"/api/appointments/{booked['id']}", json={"place_of_service": "02"}
        )

        assert moved.status_code == 200, moved.text
        assert _modality(types) == "video"

    def test_an_edit_of_something_else_teaches_nothing(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        booked = _book(booking_client, place_of_service="11")
        types.record_modality(TYPE_ID, USER, "video")

        edited = booking_client.patch(
            f"/api/appointments/{booked['id']}", json={"title": "Renamed"}
        )

        assert edited.status_code == 200, edited.text
        assert _modality(types) == "video"


class TestPerClinician:
    def test_another_clinician_s_same_named_type_is_unaffected(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        types.create(AppointmentType(id="their-session", user_id=OTHER, name="Session"))

        _book(booking_client, place_of_service="02")

        theirs = types.get("their-session", OTHER)
        assert theirs is not None
        assert theirs.modality is None

    def test_recording_against_another_clinician_s_type_changes_nothing(self) -> None:
        repo = InMemoryAppointmentTypeRepository()
        repo.create(AppointmentType(id="theirs", user_id=OTHER, name="Session"))

        repo.record_modality("theirs", USER, "video")

        theirs = repo.get("theirs", OTHER)
        assert theirs is not None
        assert theirs.modality is None


class TestTheEditorCannotSetIt:
    def test_a_patch_naming_modality_leaves_it_as_learned(
        self, booking_client: TestClient, types: InMemoryAppointmentTypeRepository
    ) -> None:
        _book(booking_client, place_of_service="02")

        response = booking_client.patch(
            f"/api/appointment-types/{TYPE_ID}", json={"modality": "in_person"}
        )

        assert response.status_code in (200, 422), response.text
        assert _modality(types) == "video"


class TestTheMapping:
    @pytest.mark.parametrize(
        ("place", "expected"),
        [("11", "in_person"), ("02", "video"), ("10", "video"), ("12", None), (None, None)],
    )
    def test_place_of_service_to_modality(self, place: str | None, expected: str | None) -> None:
        assert _modality_of(place) == expected

    @pytest.mark.parametrize(("modality", "place"), [("video", "02"), ("in_person", "11")])
    def test_a_learned_type_fills_an_unset_place(self, modality: str, place: str) -> None:
        repo = InMemoryAppointmentTypeRepository()
        repo.create(AppointmentType(id="t", user_id=USER, name="Session", modality=modality))  # type: ignore[arg-type]
        data: dict[str, object] = {"appointment_type_id": "t", "place_of_service": None}

        _apply_appointment_type(data, user_id=USER, type_repo=repo)

        assert data["place_of_service"] == place
