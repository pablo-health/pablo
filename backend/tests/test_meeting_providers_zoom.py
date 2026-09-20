# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Zoom: the guarantees on the meeting body, and reading the vendor's answer.

The create/patch/delete calls are exercised against the captured response in
``fixtures/telehealth/`` rather than an imagined one, so a rename on Zoom's
side shows up here as a test that stops passing instead of as a link the
portal never gets.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from app.meeting_providers.zoom import ZoomProvider, meeting_body, zoom_start_time
from app.meeting_providers.zoom_client import ZoomError, ZoomGrant
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.services.telehealth import ZOOM, Attendee, Clinician, Practice, TelehealthError

FIXTURES = Path(__file__).parent / "fixtures" / "telehealth"
START = datetime(2026, 10, 1, 15, 0, tzinfo=UTC)


def created_meeting() -> dict[str, Any]:
    """Zoom's documented create-meeting answer, captured and scrubbed."""
    return json.loads((FIXTURES / "zoom_create_meeting_201.json").read_text())


def an_appointment() -> Appointment:
    return Appointment(
        id=str(uuid.uuid4()),
        user_id="clinician-1",
        patient_id="patient-1",
        title="Mary Wollstonecraft",
        start_at=START,
        end_at=START + timedelta(minutes=50),
        duration_minutes=50,
        status=AppointmentStatus.CONFIRMED,
        session_type="individual",
    )


class FakeStore:
    def __init__(self, grant: ZoomGrant | None) -> None:
        self.grant = grant
        self.saved: list[ZoomGrant] = []

    def get(self, user_id: str) -> ZoomGrant | None:
        return self.grant

    def save(self, user_id: str, grant: ZoomGrant) -> None:
        self.grant = grant
        self.saved.append(grant)

    def delete(self, user_id: str) -> bool:
        self.grant = None
        return True


def a_grant(*, expires_in_minutes: int = 60) -> ZoomGrant:
    return ZoomGrant(
        access_token="access-1",  # noqa: S106 — a fixture value
        refresh_token="refresh-1",  # noqa: S106 — a fixture value
        expires_at=datetime.now(UTC) + timedelta(minutes=expires_in_minutes),
        account_handle="practice@example.test",
    )


def a_provider(store: FakeStore) -> ZoomProvider:
    return ZoomProvider(
        store=store,
        client_id="client",
        client_secret="secret",  # noqa: S106 — a fixture value
    )


class TestTheMeetingBody:
    def test_the_waiting_room_is_on_and_join_before_host_is_off(self) -> None:
        """Nobody is in the room until the clinician lets them in."""
        settings = meeting_body(an_appointment())["settings"]
        assert settings == {
            "waiting_room": True,
            "join_before_host": False,
            "meeting_authentication": False,
        }

    def test_the_topic_carries_no_patient_name(self) -> None:
        appointment = an_appointment()
        body = meeting_body(appointment)

        assert "Mary" not in json.dumps(body)
        assert "Wollstonecraft" not in json.dumps(body)
        assert appointment.patient_id not in json.dumps(body)

    def test_it_books_a_scheduled_meeting_at_the_appointment_s_time_in_utc(self) -> None:
        """A daylight-saving change must not move the room away from the slot."""
        body = meeting_body(an_appointment())
        assert body["type"] == 2
        assert body["start_time"] == "2026-10-01T15:00:00Z"
        assert body["timezone"] == "UTC"
        assert body["duration"] == 50

    def test_a_local_time_is_converted_rather_than_relabelled(self) -> None:
        appointment = an_appointment()
        appointment.start_at = datetime(2026, 10, 1, 11, 0, tzinfo=ZoneInfo("America/New_York"))
        assert zoom_start_time(appointment) == "2026-10-01T15:00:00Z"

    def test_nothing_asks_zoom_to_record(self) -> None:
        """Session audio is captured on the clinician's own machine, only."""
        assert "auto_recording" not in meeting_body(an_appointment())["settings"]


class TestCreating:
    def test_the_link_and_the_id_come_off_the_vendor_s_own_field_names(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, str, dict[str, Any] | None]] = []

        def fake_api(method: str, path: str, *, access_token: str, json_body=None):  # type: ignore[no-untyped-def]
            calls.append((method, path, json_body))
            return created_meeting()

        monkeypatch.setattr("app.meeting_providers.zoom.api_request", fake_api)
        link = a_provider(FakeStore(a_grant())).create_for_appointment(
            an_appointment(), Clinician(id="c"), Practice(), Attendee()
        )

        assert calls[0][0] == "POST"
        assert calls[0][1] == "/users/me/meetings"
        assert link is not None
        assert link.provider == ZOOM
        assert link.url == created_meeting()["join_url"]
        assert link.external_id == "81234567890"

    def test_the_fixture_still_carries_the_names_the_adapter_reads(self) -> None:
        """A rename on Zoom's side fails here rather than silently."""
        body = created_meeting()
        assert "join_url" in body
        assert "id" in body
        assert "waiting_room" in body["settings"]

    def test_a_clinician_who_has_not_connected_gets_no_meeting(self) -> None:
        provider = a_provider(FakeStore(None))
        assert provider.is_connected(Clinician(id="c")) is False
        assert (
            provider.create_for_appointment(
                an_appointment(), Clinician(id="c"), Practice(), Attendee()
            )
            is None
        )

    def test_a_vendor_refusal_is_raised_for_the_caller_to_decide_about(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: object, **kwargs: object) -> dict[str, Any]:
            raise ZoomError("nope")

        monkeypatch.setattr("app.meeting_providers.zoom.api_request", refuse)
        with pytest.raises(TelehealthError):
            a_provider(FakeStore(a_grant())).create_for_appointment(
                an_appointment(), Clinician(id="c"), Practice(), Attendee()
            )

    def test_a_meeting_with_no_link_is_a_failure_not_an_empty_link(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        body = created_meeting()
        body["join_url"] = ""
        monkeypatch.setattr("app.meeting_providers.zoom.api_request", lambda *_a, **_k: body)
        with pytest.raises(TelehealthError):
            a_provider(FakeStore(a_grant())).create_for_appointment(
                an_appointment(), Clinician(id="c"), Practice(), Attendee()
            )


class TestMovingAndCancelling:
    def test_a_reschedule_patches_the_existing_meeting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, str, dict[str, Any] | None]] = []

        def fake_api(method: str, path: str, *, access_token: str, json_body=None):  # type: ignore[no-untyped-def]
            calls.append((method, path, json_body))
            return {}

        monkeypatch.setattr("app.meeting_providers.zoom.api_request", fake_api)
        appointment = an_appointment()
        appointment.meeting_external_id = "81234567890"

        assert a_provider(FakeStore(a_grant())).reschedule(appointment, Clinician(id="c")) is True
        assert calls[0][0] == "PATCH"
        assert calls[0][1] == "/meetings/81234567890"
        assert calls[0][2] == {
            "start_time": "2026-10-01T15:00:00Z",
            "duration": 50,
            "timezone": "UTC",
        }

    def test_cancelling_deletes_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[tuple[str, str]] = []

        def fake_api(method: str, path: str, *, access_token: str, json_body=None):  # type: ignore[no-untyped-def]
            calls.append((method, path))
            return {}

        monkeypatch.setattr("app.meeting_providers.zoom.api_request", fake_api)
        assert a_provider(FakeStore(a_grant())).cancel("81234567890", Clinician(id="c")) is True
        assert calls == [("DELETE", "/meetings/81234567890")]

    def test_cancelling_after_a_disconnect_is_false_rather_than_an_error(self) -> None:
        assert a_provider(FakeStore(None)).cancel("81234567890", Clinician(id="c")) is False


class TestTheGrant:
    def test_a_spent_grant_is_refreshed_and_the_new_one_stored_before_use(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Zoom retires the refresh token it was shown; keeping the old one
        leaves a connection that works exactly once."""
        store = FakeStore(a_grant(expires_in_minutes=0))
        rotated = ZoomGrant(
            access_token="access-2",  # noqa: S106 — a fixture value
            refresh_token="refresh-2",  # noqa: S106 — a fixture value
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        used: list[str] = []

        monkeypatch.setattr("app.meeting_providers.zoom.refresh_grant", lambda **_kwargs: rotated)

        def fake_api(method: str, path: str, *, access_token: str, json_body=None):  # type: ignore[no-untyped-def]
            used.append(access_token)
            return created_meeting()

        monkeypatch.setattr("app.meeting_providers.zoom.api_request", fake_api)
        a_provider(store).create_for_appointment(
            an_appointment(), Clinician(id="c"), Practice(), Attendee()
        )

        assert store.saved == [rotated]
        assert used == ["access-2"]

    def test_a_refresh_that_fails_reads_as_not_connected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(**kwargs: object) -> ZoomGrant:
            raise ZoomError("expired")

        monkeypatch.setattr("app.meeting_providers.zoom.refresh_grant", refuse)
        link = a_provider(FakeStore(a_grant(expires_in_minutes=0))).create_for_appointment(
            an_appointment(), Clinician(id="c"), Practice(), Attendee()
        )
        assert link is None

    def test_a_grant_is_refreshed_before_it_actually_expires(self) -> None:
        """A call that starts just under the wire must not finish just over."""
        assert a_grant(expires_in_minutes=1).is_expired() is True
        assert a_grant(expires_in_minutes=10).is_expired() is False
