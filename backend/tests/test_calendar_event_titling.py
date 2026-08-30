# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for how pushed calendar events are titled, and for narrowing that choice."""

from __future__ import annotations

import ast
import base64
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Generator

    from fastapi.testclient import TestClient

import pytest
from app.calendar_providers.event_titles import (
    DEFAULT_EVENT_SUMMARY,
    EventTitleStyle,
    initials_by_patient,
    parse_style,
    summary_for,
)
from app.main import app
from app.models import AuditAction
from app.models.patient import Patient
from app.repositories.google_calendar_token import GoogleCalendarTokenDoc
from app.routes.scheduling import get_google_calendar_service
from app.scheduling_engine.models.appointment import Appointment
from app.services import get_audit_service
from app.services.google_calendar_service import GoogleCalendarService, RetitleOutcome
from app.settings import get_settings

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
PATIENT_NAME = ("Zorbulax", "Quintwhistle")


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patient(patient_id: str, first: str, last: str) -> Patient:
    return Patient(
        id=patient_id,
        first_name=first,
        last_name=last,
        created_at=NOW,
        updated_at=NOW,
    )


def _appointment(patient_id: str, *, start: datetime, event_id: str | None = None) -> Appointment:
    return Appointment(
        id=f"appt-{patient_id}-{start:%Y%m%d%H%M}",
        user_id="user-001",
        patient_id=patient_id,
        title="Session",
        start_at=start,
        end_at=start + timedelta(minutes=50),
        duration_minutes=50,
        status="confirmed",
        session_type="individual",
        created_at=NOW,
        google_event_id=event_id,
    )


# The naming itself


class TestInitials:
    def test_initials_read_as_first_and_last(self) -> None:
        labels = initials_by_patient([_patient("p1", "Jane", "Miller")])
        assert labels["p1"] == "J.M."

    def test_accents_fold_to_letters_a_calendar_can_render(self) -> None:
        labels = initials_by_patient([_patient("p1", "Émile", "Ångström")])
        assert labels["p1"] == "E.A."

    def test_two_of_the_same_initials_are_told_apart(self) -> None:
        """A therapist seeing "J.M." twice is worse off than with the
        generic wording they replaced."""
        labels = initials_by_patient(
            [_patient("p1", "Jane", "Miller"), _patient("p2", "Jon", "Moss")]
        )
        assert labels["p1"] != labels["p2"]
        assert labels["p1"] == "J.Mi."
        assert labels["p2"] == "J.Mo."

    def test_the_same_name_twice_still_gets_distinguishable_labels(self) -> None:
        labels = initials_by_patient(
            [_patient("p1", "Jane", "Miller"), _patient("p2", "Jane", "Miller")]
        )
        assert labels["p1"] != labels["p2"]
        assert labels["p1"].startswith("J.M.")
        assert labels["p2"].startswith("J.M.")

    def test_a_patient_alone_keeps_the_short_form(self) -> None:
        labels = initials_by_patient(
            [_patient("p1", "Jane", "Miller"), _patient("p2", "Sam", "Okafor")]
        )
        assert labels == {"p1": "J.M.", "p2": "S.O."}


class TestSummaryForStyle:
    def test_generic_is_exactly_the_floor_wording(self) -> None:
        patient = _patient("p1", *PATIENT_NAME)
        assert summary_for(EventTitleStyle.GENERIC, patient) == "Therapy Session"

    def test_full_is_the_patient_name(self) -> None:
        patient = _patient("p1", "Jane", "Miller")
        assert summary_for(EventTitleStyle.FULL, patient) == "Jane Miller"

    def test_a_missing_patient_falls_back_to_the_floor(self) -> None:
        """Never a blank title, and never a guess upward from half a name."""
        assert summary_for(EventTitleStyle.FULL, None) == DEFAULT_EVENT_SUMMARY

    def test_an_unreadable_stored_style_reads_as_the_floor(self) -> None:
        assert parse_style("something-else") is EventTitleStyle.GENERIC
        assert parse_style(None) is EventTitleStyle.GENERIC


# Pushing


class _FakeEvents:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []
        self.patched: list[dict[str, Any]] = []
        self.fail_on: set[str] = set()

    def insert(self, **kwargs: Any) -> Any:
        self.inserted.append(kwargs)
        return _FakeRequest({"id": "gcal-event-1"})

    def patch(self, **kwargs: Any) -> Any:
        self.patched.append(kwargs)
        if kwargs["eventId"] in self.fail_on:
            return _FakeRequest(None, boom=True)
        return _FakeRequest({"id": kwargs["eventId"]})


class _FakeRequest:
    def __init__(self, payload: Any, *, boom: bool = False) -> None:
        self._payload = payload
        self._boom = boom

    def execute(self) -> Any:
        if self._boom:
            raise RuntimeError("calendar refused")
        return self._payload


class _FakeCalendar:
    def __init__(self) -> None:
        self.events_resource = _FakeEvents()

    def events(self) -> _FakeEvents:
        return self.events_resource


@pytest.fixture
def token_repo() -> MagicMock:
    repo = MagicMock()
    repo.get.return_value = GoogleCalendarTokenDoc(
        user_id="user-001",
        encrypted_tokens="encrypted",
        calendar_id="primary@gmail.com",
        event_titling="initials",
    )
    return repo


@pytest.fixture
def patient_repo() -> MagicMock:
    repo = MagicMock()
    repo.list_by_user.return_value = ([_patient("p1", *PATIENT_NAME)], 1)
    return repo


@pytest.fixture
def appointment_repo() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(
    token_repo: MagicMock,
    appointment_repo: MagicMock,
    patient_repo: MagicMock,
) -> GoogleCalendarService:
    return GoogleCalendarService(
        token_repo=token_repo,
        appointment_repo=appointment_repo,
        client_id="test-client-id",
        client_secret="test-client-secret",  # noqa: S106
        patient_repo=patient_repo,
    )


def _with_calendar(service: GoogleCalendarService, fake: _FakeCalendar) -> Any:
    creds = MagicMock(expired=False)
    return patch.multiple(
        "app.services.google_calendar_service",
        decrypt_tokens=MagicMock(return_value={}),
        _make_credentials=MagicMock(return_value=creds),
        _build_calendar_service=MagicMock(return_value=fake),
        _now=MagicMock(return_value=NOW),
    )


class TestPushedTitles:
    @pytest.mark.parametrize(
        ("style", "expected"),
        [
            ("generic", "Therapy Session"),
            ("initials", "Z.Q."),
            ("full", "Zorbulax Quintwhistle"),
        ],
    )
    def test_a_pushed_event_reads_as_the_chosen_style(
        self,
        style: str,
        expected: str,
        service: GoogleCalendarService,
        token_repo: MagicMock,
    ) -> None:
        token_repo.get.return_value.event_titling = style
        fake = _FakeCalendar()

        with _with_calendar(service, fake):
            service.push_appointment("user-001", _appointment("p1", start=NOW + timedelta(days=1)))

        assert fake.events_resource.inserted[0]["body"]["summary"] == expected

    def test_without_a_patient_repository_the_floor_is_used(
        self,
        token_repo: MagicMock,
        appointment_repo: MagicMock,
    ) -> None:
        """A deployment that can't read names never accidentally sends one."""
        token_repo.get.return_value.event_titling = "full"
        bare = GoogleCalendarService(
            token_repo=token_repo,
            appointment_repo=appointment_repo,
            client_id="id",
            client_secret="secret",  # noqa: S106
        )
        fake = _FakeCalendar()

        with _with_calendar(bare, fake):
            bare.push_appointment("user-001", _appointment("p1", start=NOW + timedelta(days=1)))

        assert fake.events_resource.inserted[0]["body"]["summary"] == "Therapy Session"


class TestRetitleOnNarrowing:
    def test_narrowing_rewrites_future_events_and_leaves_the_past_alone(
        self,
        service: GoogleCalendarService,
        token_repo: MagicMock,
        appointment_repo: MagicMock,
    ) -> None:
        """The setting changing while the names stay in Google would make
        the control a lie."""
        token_repo.get.return_value.event_titling = "generic"
        future = [
            _appointment("p1", start=NOW + timedelta(days=1), event_id="evt-future-1"),
            _appointment("p1", start=NOW + timedelta(days=8), event_id="evt-future-2"),
        ]
        appointment_repo.list_by_range.return_value = future
        fake = _FakeCalendar()

        with _with_calendar(service, fake):
            outcome = service.retitle_future_events("user-001")

        assert outcome.retitled == 2
        assert {call["eventId"] for call in fake.events_resource.patched} == {
            "evt-future-1",
            "evt-future-2",
        }
        assert all(
            call["body"]["summary"] == "Therapy Session" for call in fake.events_resource.patched
        )
        # The window asked for starts at now — the past is never fetched.
        assert appointment_repo.list_by_range.call_args[0][1] == NOW

    def test_an_appointment_never_pushed_is_not_touched(
        self,
        service: GoogleCalendarService,
        appointment_repo: MagicMock,
    ) -> None:
        appointment_repo.list_by_range.return_value = [
            _appointment("p1", start=NOW + timedelta(days=1)),
        ]
        fake = _FakeCalendar()

        with _with_calendar(service, fake):
            outcome = service.retitle_future_events("user-001")

        assert outcome.retitled == 0
        assert fake.events_resource.patched == []

    def test_one_event_that_will_not_update_does_not_strand_the_rest(
        self,
        service: GoogleCalendarService,
        appointment_repo: MagicMock,
    ) -> None:
        appointment_repo.list_by_range.return_value = [
            _appointment("p1", start=NOW + timedelta(days=1), event_id="evt-1"),
            _appointment("p1", start=NOW + timedelta(days=8), event_id="evt-stuck"),
            _appointment("p1", start=NOW + timedelta(days=15), event_id="evt-3"),
        ]
        fake = _FakeCalendar()
        fake.events_resource.fail_on = {"evt-stuck"}

        with _with_calendar(service, fake):
            outcome = service.retitle_future_events("user-001")

        assert outcome.retitled == 2
        assert outcome.failed == 1

    def test_retitling_never_says_what_the_events_now_read(
        self,
        service: GoogleCalendarService,
        token_repo: MagicMock,
        appointment_repo: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """HIPAA: counts only, on a path whose whole subject is names."""
        token_repo.get.return_value.event_titling = "full"
        appointment_repo.list_by_range.return_value = [
            _appointment("p1", start=NOW + timedelta(days=1), event_id="evt-1"),
        ]
        fake = _FakeCalendar()

        with (
            caplog.at_level(logging.DEBUG, logger="app.services.google_calendar_service"),
            _with_calendar(service, fake),
        ):
            service.retitle_future_events("user-001")

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert PATIENT_NAME[0] not in logged
        assert PATIENT_NAME[1] not in logged
        # It did write the name to the calendar, which is what was chosen.
        assert fake.events_resource.patched[0]["body"]["summary"] == "Zorbulax Quintwhistle"


# Routes


class TestTitlingRoute:
    @staticmethod
    def _wire(status: dict[str, Any], retitled: int = 0) -> MagicMock:
        gcal = MagicMock()
        gcal.get_sync_status.return_value = status
        gcal.set_event_titling.return_value = True
        gcal.retitle_future_events.return_value = RetitleOutcome(retitled, 0, 0)
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal
        return gcal

    def test_choosing_full_without_confirming_writes_nothing(self, client: TestClient) -> None:
        """The attestation is the thing that permits the disclosure — a
        request without it must not leave the preference stored anyway."""
        gcal = self._wire({"connected": True, "event_titling": "initials", "calendar_id": "a@b.c"})

        response = client.put(
            "/api/google-calendar/event-titling",
            json={"style": "full", "attested": False},
        )

        assert response.status_code == 400, response.text
        gcal.set_event_titling.assert_not_called()

    def test_choosing_full_with_confirmation_is_recorded_as_evidence(
        self,
        client: TestClient,
        audit_spy: MagicMock,
    ) -> None:
        self._wire({"connected": True, "event_titling": "initials", "calendar_id": "jane@x.test"})

        response = client.put(
            "/api/google-calendar/event-titling",
            json={"style": "full", "attested": True},
        )

        assert response.status_code == 200, response.text
        audit_spy.log.assert_called_once()
        action, user, _request = audit_spy.log.call_args[0]
        assert action is AuditAction.CALENDAR_NAME_DISCLOSURE_ATTESTED
        assert user.id == "test-user-123"
        changes = audit_spy.log.call_args.kwargs["changes"]
        assert changes["calendar_account"] == "jane@x.test"
        assert changes["event_titling"] == "full"

    def test_the_lower_rungs_need_no_attestation_and_record_none(
        self,
        client: TestClient,
        audit_spy: MagicMock,
    ) -> None:
        self._wire({"connected": True, "event_titling": "initials", "calendar_id": "a@b.c"})

        response = client.put(
            "/api/google-calendar/event-titling",
            json={"style": "generic"},
        )

        assert response.status_code == 200, response.text
        audit_spy.log.assert_not_called()

    @pytest.mark.parametrize(
        ("previous", "chosen", "expect_retitle"),
        [
            ("full", "initials", True),
            ("full", "generic", True),
            ("initials", "generic", True),
            ("generic", "initials", False),
            ("initials", "initials", False),
        ],
    )
    def test_only_narrowing_rewrites_what_is_already_there(
        self,
        client: TestClient,
        previous: str,
        chosen: str,
        expect_retitle: bool,
    ) -> None:
        gcal = self._wire({"connected": True, "event_titling": previous, "calendar_id": "a@b.c"})

        client.put(
            "/api/google-calendar/event-titling",
            json={"style": chosen, "attested": True},
        )

        assert gcal.retitle_future_events.called is expect_retitle

    def test_an_unknown_style_is_refused(self, client: TestClient) -> None:
        gcal = self._wire({"connected": True, "event_titling": "initials", "calendar_id": "a@b.c"})

        response = client.put("/api/google-calendar/event-titling", json={"style": "surname_only"})

        assert response.status_code == 400, response.text
        gcal.set_event_titling.assert_not_called()

    def test_setting_it_without_a_connection_is_a_not_found(self, client: TestClient) -> None:
        self._wire({"connected": False})

        response = client.put("/api/google-calendar/event-titling", json={"style": "generic"})

        assert response.status_code == 404, response.text

    def test_connecting_defaults_to_initials(self, client: TestClient) -> None:
        gcal = MagicMock()
        app.dependency_overrides[get_google_calendar_service] = lambda: gcal

        client.get(
            "/api/google-calendar/callback",
            params={
                "code": "auth-code",
                "state": "signed",
                "redirect_uri": "http://localhost:3000/dashboard/settings/calendar",
            },
        )

        assert gcal.handle_callback.call_args.kwargs["event_titling"] is EventTitleStyle.INITIALS


def test_a_pablo_owned_event_is_still_identified_only_by_its_stored_id() -> None:
    """Titles are for people to read. Matching an event on one would break
    the moment a therapist changed how their events read — which they now
    can, which is the whole of this change.

    One comparison against a ``summary`` is legitimate and has to stay: the
    app-calendar choice finds the calendar Pablo made by its name. That is a
    calendar's name, not an event's title, and nothing a therapist can
    retitle. It is pinned to that one function so it can't quietly spread.
    """
    source = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "google_calendar_service.py"
    ).read_text()

    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        body = ast.get_source_segment(source, node) or ""
        if '"summary"' not in body or "==" not in body:
            continue
        if any(f'"summary"{op}' in body.replace(" ", "") for op in ("==", "in(")) or (
            'get("summary")' in body and "==" in body
        ):
            offenders.append(node.name)

    assert offenders == ["_get_or_create_app_calendar_id"], (
        f"an event may be being identified by its title, in: {offenders}"
    )

    # And the identifier that actually does the matching is the stored id.
    assert "eventId=appointment.google_event_id" in source


@pytest.fixture
def audit_spy() -> MagicMock:
    spy = MagicMock()
    app.dependency_overrides[get_audit_service] = lambda: spy
    return spy
