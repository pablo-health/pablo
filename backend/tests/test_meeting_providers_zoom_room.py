# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A clinician who uses Zoom pastes their own Zoom room once.

A deployment with no Zoom app cannot make a meeting per appointment, so Zoom
there is the clinician's own room: offered once they have pasted one, and
handed to every video appointment on Zoom. Where the deployment does have a
Zoom app and the clinician connected it, a meeting per appointment wins.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from app.main import app
from app.meeting_providers.registry import build_registry
from app.meeting_providers.zoom_room import ZoomAppOrRoom, ZoomRoomProvider, is_zoom_room_url
from app.models import UserPreferences
from app.routes.scheduling import _clinician_view
from app.routes.telehealth import get_zoom_store
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.telehealth import (
    MANUAL,
    ZOOM,
    Attendee,
    Clinician,
    MeetingLink,
    Practice,
    clinician_from_preferences,
    provision_meeting,
)
from app.settings import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.repositories import InMemoryUserRepository
    from fastapi.testclient import TestClient

ROOM = "https://us02web.zoom.us/j/81234567890?pwd=abcDEF123"
START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
#: Telehealth in the patient's home, the place of service a video visit carries.
TELEHEALTH_HOME = "10"


def an_appointment(**overrides: object) -> Appointment:
    fields: dict[str, object] = {
        "id": str(uuid.uuid4()),
        "user_id": "clinician-1",
        "patient_id": "patient-1",
        "title": "Session",
        "start_at": START,
        "end_at": START + timedelta(minutes=50),
        "duration_minutes": 50,
        "status": AppointmentStatus.CONFIRMED,
        "session_type": "individual",
        "place_of_service": TELEHEALTH_HOME,
    }
    fields.update(overrides)
    return Appointment(**fields)  # type: ignore[arg-type]


def a_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"telehealth_providers_enabled": "manual,zoom"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class FakeZoomStore:
    def get(self, user_id: str) -> None:
        return None

    def save(self, user_id: str, grant: object) -> None:
        return None

    def delete(self, user_id: str) -> bool:
        return False


class FakeZoomApp:
    """A Zoom app that makes a meeting per appointment, connected or not."""

    provider_id = ZOOM
    display_name = "Zoom"

    def __init__(self, *, connected: bool) -> None:
        self.connected = connected
        self.cancelled: list[str] = []

    def is_connected(self, _clinician: Clinician) -> bool:
        return self.connected

    def create_for_appointment(
        self,
        _appointment: Appointment,
        _clinician: Clinician,
        _practice: Practice,
        _attendee: Attendee,
    ) -> MeetingLink:
        return MeetingLink(url="https://zoom.us/j/999", provider=ZOOM, external_id="999")

    def cancel(self, external_id: str, _clinician: Clinician) -> bool:
        self.cancelled.append(external_id)
        return True


#: Shared with the card's check (frontend/src/lib/api/__tests__/zoomRoomUrl.test.ts),
#: so the two can't drift apart: a card that accepts what the server refuses
#: leaves the clinician on a "Try again" that never works.
_CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "zoom_room_url_cases.json").read_text(encoding="utf-8")
)


class TestWhatCountsAsAZoomRoom:
    @pytest.mark.parametrize("url", _CASES["accept"])
    def test_an_https_address_on_zoom_is_accepted(self, url: str) -> None:
        assert is_zoom_room_url(url)

    @pytest.mark.parametrize("url", _CASES["refuse"])
    def test_anything_else_is_refused(self, url: str) -> None:
        assert not is_zoom_room_url(url)


class TestThePastedRoom:
    def test_no_room_means_not_connected_and_no_link(self) -> None:
        provider = ZoomRoomProvider()
        clinician = Clinician(id="c")
        assert not provider.is_connected(clinician)
        link = provider.create_for_appointment(an_appointment(), clinician, Practice(), Attendee())
        assert link is None

    def test_the_room_is_handed_over_as_pasted_passcode_and_all(self) -> None:
        """Zoom's ``?pwd=`` lets the patient in without typing the passcode."""
        link = ZoomRoomProvider().create_for_appointment(
            an_appointment(),
            Clinician(id="c", zoom_room_url=ROOM),
            Practice(),
            Attendee(display_name="Sam"),
        )
        assert link == MeetingLink(url=ROOM, provider=ZOOM, external_id=None)

    def test_the_doxy_room_is_not_a_zoom_room(self) -> None:
        assert not ZoomRoomProvider().is_connected(Clinician(id="c", room_url=ROOM))

    def test_there_is_nothing_to_cancel(self) -> None:
        assert ZoomRoomProvider().cancel("anything", Clinician(id="c", zoom_room_url=ROOM)) is False


class TestWithNoZoomApp:
    """Acceptance 1 and 2: Zoom is the pasted room, and only once there is one."""

    def test_zoom_is_the_pasted_room(self) -> None:
        registry = build_registry(a_settings(), zoom_store=FakeZoomStore())
        assert isinstance(registry.get(ZOOM), ZoomRoomProvider)

    def test_without_a_pasted_room_zoom_is_not_offered(self) -> None:
        registry = build_registry(a_settings())
        assert registry.offered_to(Clinician(id="c")) == (MANUAL,)

    def test_with_one_it_is_offered_and_every_video_appointment_gets_it(self) -> None:
        registry = build_registry(a_settings())
        clinician = Clinician(id="c", zoom_room_url=ROOM)
        assert registry.offered_to(clinician) == (ZOOM, MANUAL)

        for _ in range(2):
            booked = provision_meeting(
                an_appointment(),
                clinician=clinician,
                practice=Practice(),
                registry=registry,
                requested_provider=ZOOM,
            )
            assert (booked.provider, booked.video_link) == (ZOOM, ROOM)

    def test_a_stored_zoom_default_needs_no_further_change(self) -> None:
        """Acceptance 5: the default was zoom all along; pasting a room is enough."""
        preferences = UserPreferences(default_video_platform=ZOOM, zoom_room_url=ROOM)
        booked = provision_meeting(
            an_appointment(),
            clinician=clinician_from_preferences("c", preferences),
            practice=Practice(),
            registry=build_registry(a_settings()),
        )
        assert (booked.provider, booked.video_link) == (ZOOM, ROOM)

    def test_an_in_person_appointment_still_gets_no_room(self) -> None:
        preferences = UserPreferences(default_video_platform=ZOOM, zoom_room_url=ROOM)
        booked = provision_meeting(
            an_appointment(place_of_service="11"),
            clinician=clinician_from_preferences("c", preferences),
            practice=Practice(),
            registry=build_registry(a_settings()),
        )
        assert booked.video_link is None

    def test_zoom_left_off_the_deployment_list_is_never_built(self) -> None:
        registry = build_registry(a_settings(telehealth_providers_enabled="manual"))
        assert registry.get(ZOOM) is None


class TestWithAZoomApp:
    """Acceptance 4: a connected Zoom app makes a meeting per appointment."""

    def test_the_deployment_builds_the_app_first(self) -> None:
        registry = build_registry(a_settings(zoom_client_id="abc"), zoom_store=FakeZoomStore())
        assert isinstance(registry.get(ZOOM), ZoomAppOrRoom)

    def test_connected_the_app_wins_over_a_pasted_room(self) -> None:
        app_ = FakeZoomApp(connected=True)
        link = ZoomAppOrRoom(app_).create_for_appointment(  # type: ignore[arg-type]
            an_appointment(), Clinician(id="c", zoom_room_url=ROOM), Practice(), Attendee()
        )
        assert link == MeetingLink(url="https://zoom.us/j/999", provider=ZOOM, external_id="999")

    def test_not_connected_the_pasted_room_is_used(self) -> None:
        provider = ZoomAppOrRoom(FakeZoomApp(connected=False))  # type: ignore[arg-type]
        clinician = Clinician(id="c", zoom_room_url=ROOM)
        assert provider.is_connected(clinician)
        link = provider.create_for_appointment(an_appointment(), clinician, Practice(), Attendee())
        assert link == MeetingLink(url=ROOM, provider=ZOOM, external_id=None)

    def test_neither_means_not_offered(self) -> None:
        provider = ZoomAppOrRoom(FakeZoomApp(connected=False))  # type: ignore[arg-type]
        assert not provider.is_connected(Clinician(id="c"))

    def test_cancelling_goes_to_the_app(self) -> None:
        app_ = FakeZoomApp(connected=True)
        assert ZoomAppOrRoom(app_).cancel("999", Clinician(id="c"))  # type: ignore[arg-type]
        assert app_.cancelled == ["999"]


class TestTheSchedulingPathReadsTheRoom:
    def test_the_clinician_view_carries_the_pasted_zoom_room(
        self, mock_user_repo: InMemoryUserRepository
    ) -> None:
        mock_user_repo.update_preferences("c", {"zoom_room_url": ROOM})
        assert _clinician_view(mock_user_repo, "c").zoom_room_url == ROOM


@pytest.fixture
def zoom_without_an_app() -> Iterator[None]:
    app.dependency_overrides[get_zoom_store] = FakeZoomStore
    with patch("app.routes.telehealth.get_settings", return_value=a_settings()):
        yield
    app.dependency_overrides.pop(get_zoom_store, None)


@pytest.mark.usefixtures("zoom_without_an_app")
class TestTheSettingsRoutes:
    def test_a_zoom_room_is_saved_and_zoom_becomes_ready(
        self, client: TestClient, mock_user_repo: InMemoryUserRepository, mock_user_id: str
    ) -> None:
        response = client.put("/api/telehealth/zoom-room-url", json={"zoom_room_url": f" {ROOM} "})
        assert response.status_code == 200
        assert response.json() == {"zoom_room_url": ROOM}
        assert mock_user_repo.get_preferences(mock_user_id).zoom_room_url == ROOM

        providers = client.get("/api/telehealth/providers").json()
        assert providers["zoom_room_url"] == ROOM
        assert providers["zoom_app"] is False
        zoom = next(p for p in providers["providers"] if p["id"] == ZOOM)
        assert zoom["connected"] is True

    def test_before_a_room_is_pasted_zoom_is_listed_but_not_ready(self, client: TestClient) -> None:
        providers = client.get("/api/telehealth/providers").json()
        zoom = next(p for p in providers["providers"] if p["id"] == ZOOM)
        assert zoom["connected"] is False
        assert providers["zoom_room_url"] is None

    @pytest.mark.parametrize(
        "url",
        [
            "http://zoom.us/j/1",
            "https://zoom.us.example.com/j/1",
            "https://doxy.me/x",
            "https://evil.example\\.zoom.us/j/1",
        ],
    )
    def test_an_address_that_is_not_zoom_is_a_field_error(
        self,
        client: TestClient,
        mock_user_repo: InMemoryUserRepository,
        mock_user_id: str,
        url: str,
    ) -> None:
        """Acceptance 3: refused, naming the field, and nothing is saved."""
        response = client.put("/api/telehealth/zoom-room-url", json={"zoom_room_url": url})
        assert response.status_code == 422
        assert "zoom_room_url" in response.text
        assert mock_user_repo.get_preferences(mock_user_id).zoom_room_url is None

    def test_an_empty_address_removes_the_room(
        self, client: TestClient, mock_user_repo: InMemoryUserRepository, mock_user_id: str
    ) -> None:
        mock_user_repo.update_preferences(mock_user_id, {"zoom_room_url": ROOM})
        response = client.put("/api/telehealth/zoom-room-url", json={"zoom_room_url": "  "})
        assert response.status_code == 200
        assert mock_user_repo.get_preferences(mock_user_id).zoom_room_url is None


class TestTheProvidersRouteSaysWhetherThereIsAnApp:
    def test_with_a_zoom_app_the_card_offers_connect(self, client: TestClient) -> None:
        """With an app configured the card shows "Connect Zoom", not the room field."""
        app.dependency_overrides[get_zoom_store] = FakeZoomStore
        settings = a_settings(zoom_client_id="client-id")
        try:
            with patch("app.routes.telehealth.get_settings", return_value=settings):
                providers = client.get("/api/telehealth/providers").json()
        finally:
            app.dependency_overrides.pop(get_zoom_store, None)
        assert providers["zoom_app"] is True


class TestEveryRouteThatSavesItChecksIt:
    """The general preferences PATCH must not be a way round the zoom.us check."""

    def test_patching_preferences_with_a_non_zoom_address_is_refused(
        self, client: TestClient, mock_user_repo: InMemoryUserRepository, mock_user_id: str
    ) -> None:
        response = client.patch(
            "/api/users/me/preferences", json={"zoom_room_url": "javascript:alert(1)//zoom.us"}
        )
        assert response.status_code == 422
        assert "zoom_room_url" in response.text
        assert mock_user_repo.get_preferences(mock_user_id).zoom_room_url is None

    def test_patching_preferences_with_a_backslash_host_is_refused(
        self, client: TestClient, mock_user_repo: InMemoryUserRepository, mock_user_id: str
    ) -> None:
        """A browser reads ``\\`` as ``/``, so this opens evil.example, not Zoom."""
        response = client.patch(
            "/api/users/me/preferences",
            json={"zoom_room_url": "https://evil.example\\.zoom.us/j/1"},
        )
        assert response.status_code == 422
        assert mock_user_repo.get_preferences(mock_user_id).zoom_room_url is None

    def test_patching_preferences_with_a_zoom_room_saves_it(
        self, client: TestClient, mock_user_repo: InMemoryUserRepository, mock_user_id: str
    ) -> None:
        response = client.patch("/api/users/me/preferences", json={"zoom_room_url": ROOM})
        assert response.status_code == 200
        assert mock_user_repo.get_preferences(mock_user_id).zoom_room_url == ROOM
