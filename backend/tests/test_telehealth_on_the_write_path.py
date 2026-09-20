# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Booking an appointment on a video service, end to end through the route.

The seam's own tests prove the rules; these prove the wiring — that the route
asks the registry, persists what comes back, releases the meeting when the
appointment is cancelled, and never fails a booking over a vendor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from app.main import app
from app.routes import scheduling
from app.routes.scheduling import (
    get_availability_rule_repository,
    get_meeting_registry,
    get_scheduling_service,
)
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.repositories.availability_rule import (
    InMemoryAvailabilityRuleRepository,
)
from app.scheduling_engine.services.availability import AvailabilityEngine
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.telehealth import (
    DOXY_ME,
    MANUAL,
    ZOOM,
    Attendee,
    Clinician,
    MeetingLink,
    MeetingProviderRegistry,
    Practice,
    TelehealthError,
)

if TYPE_CHECKING:
    from fastapi.testclient import TestClient


def _create_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "patient_id": "patient-1",
        "title": "Weekly check-in",
        "start_at": "2026-04-15T14:00:00Z",
        "end_at": "2026-04-15T14:50:00Z",
        "duration_minutes": 50,
    }
    payload.update(overrides)
    return payload


class RecordingProvider:
    def __init__(
        self,
        provider_id: str,
        *,
        link: MeetingLink | None,
        raises: bool = False,
    ) -> None:
        self.provider_id = provider_id
        self.display_name = provider_id
        self._link = link
        self._raises = raises
        self.attendees: list[Attendee] = []
        self.cancelled: list[str] = []

    def is_connected(self, clinician: Clinician) -> bool:
        return True

    def create_for_appointment(
        self,
        appointment,  # type: ignore[no-untyped-def]
        clinician: Clinician,
        practice: Practice,
        attendee: Attendee,
    ) -> MeetingLink | None:
        if self._raises:
            raise TelehealthError("vendor said no")
        self.attendees.append(attendee)
        return self._link

    def cancel(self, external_id: str, clinician: Clinician) -> bool:
        self.cancelled.append(external_id)
        return True


@pytest.fixture
def write_client(client: TestClient) -> TestClient:
    """The real SchedulingService over in-memory repos, as the route tests use."""
    appt_repo = InMemoryAppointmentRepository()
    rule_repo = InMemoryAvailabilityRuleRepository()
    engine = AvailabilityEngine(rule_repo, appt_repo)
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appt_repo, engine)
    app.dependency_overrides[get_availability_rule_repository] = lambda: rule_repo
    return client


def _registry(provider: RecordingProvider) -> None:
    app.dependency_overrides[get_meeting_registry] = lambda: MeetingProviderRegistry([provider])


class TestBookingGetsARoom:
    def test_the_provider_s_link_and_handle_are_persisted(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        response = write_client.post("/api/appointments", json=_create_payload())

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["provider"] == ZOOM
        assert body["video_link"] == "https://z.test/81"
        assert body["meeting_external_id"] == "81"

    def test_a_pasted_link_is_not_replaced_by_the_preference(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        payload = _create_payload()
        payload["video_link"] = "https://practice.test/my-room"
        response = write_client.post("/api/appointments", json=payload)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["video_link"] == "https://practice.test/my-room"
        assert body["provider"] == MANUAL
        assert provider.attendees == []

    def test_a_vendor_failure_never_fails_the_booking(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Somebody is expected at a time. The missing piece is a link."""
        _registry(RecordingProvider(ZOOM, link=None, raises=True))
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        response = write_client.post("/api/appointments", json=_create_payload())

        assert response.status_code == 201, response.text
        assert response.json()["video_link"] is None

    def test_only_a_first_name_reaches_the_provider(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            DOXY_ME, link=MeetingLink(url="https://d.test/r", provider=DOXY_ME, external_id="h")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=DOXY_ME),
        )
        monkeypatch.setattr(
            scheduling, "_attendee_view", lambda _repo, _appt: Attendee(display_name="Mary")
        )

        response = write_client.post("/api/appointments", json=_create_payload())

        assert response.status_code == 201, response.text
        assert provider.attendees == [Attendee(display_name="Mary")]


class TestCancelling:
    def test_the_vendor_meeting_is_released(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(
            ZOOM, link=MeetingLink(url="https://z.test/81", provider=ZOOM, external_id="81")
        )
        _registry(provider)
        monkeypatch.setattr(
            scheduling,
            "_clinician_view",
            lambda _repo, user_id: Clinician(id=user_id, preferred_provider=ZOOM),
        )

        created = write_client.post("/api/appointments", json=_create_payload())
        assert created.status_code == 201, created.text

        cancelled = write_client.delete(f"/api/appointments/{created.json()['id']}")

        assert cancelled.status_code == 200, cancelled.text
        assert provider.cancelled == ["81"]

    def test_an_in_person_appointment_releases_nothing(
        self, write_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = RecordingProvider(ZOOM, link=None)
        _registry(provider)
        monkeypatch.setattr(
            scheduling, "_clinician_view", lambda _repo, user_id: Clinician(id=user_id)
        )

        created = write_client.post("/api/appointments", json=_create_payload())
        assert created.status_code == 201, created.text

        write_client.delete(f"/api/appointments/{created.json()['id']}")

        assert provider.cancelled == []
