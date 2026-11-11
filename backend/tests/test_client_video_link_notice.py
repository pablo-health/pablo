# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The client is sent the video link at booking, and again when it changes (PABLO-en9h.9).

Through the real routes: a clinician booking and editing, each path a client's
booking-link booking is confirmed by, and the late Meet read. The practice's
facts and the mail are fakes; whether the link may go out at all is the real
:func:`app.services.telehealth.client_join_link`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import pytest
from app.main import app
from app.models import Patient
from app.repositories.patient import InMemoryPatientRepository
from app.routes import scheduling
from app.routes.scheduling import (
    _fill_meet_link,
    get_owner_timezone,
    get_patient_repository,
    get_scheduling_service,
)
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.scheduling_engine.services.scheduling import SchedulingService
from app.services.client_video_link_notice import (
    KIND_BOOKED,
    KIND_CHANGED,
    ClientVideoLinkNotifier,
    PracticeFacts,
    compose,
    describe_when,
    get_client_video_link_notifier,
)
from app.services.email_sender import InMemoryEmailSender, NoneEmailSender, OutboundEmail
from app.services.telehealth import GOOGLE_MEET

from . import test_client_booking_gets_a_room as room_tests
from .test_client_booking_gets_a_room import PATHS, _client, _zoom
from .test_client_booking_gets_a_room import link_repo as link_repo  # noqa: PLC0414  # fixture
from .test_telehealth_on_the_write_path import (
    MEET_LINK,
    InlineFollowUp,
    SlowMeetCalendar,
    _create_payload,
)
from .test_telehealth_on_the_write_path import write_client as write_client  # noqa: PLC0414

if TYPE_CHECKING:
    from app.repositories.booking_link import InMemoryBookingLinkRepository
    from fastapi.testclient import TestClient

PRACTICE_NAME = "Riverside Counseling"
PORTAL = "https://portal.test/p/riverside"
CLIENT_EMAIL = "jane@example.com"
#: A pasted Doxy room, with the first name Doxy checks the client in by.
DOXY_ROOM = "https://doxy.me/riverside?username=Jane&pid=7"
DOXY_SENT = "https://doxy.me/riverside?pid=7"
NEW_ROOM = "https://doxy.me/riverside-2"
EASTERN = ZoneInfo("America/New_York")
#: The clock the clinician-route tests run at: before every session they book
#: (``_create_payload`` books 2026-04-15 14:00-14:50Z).
BEFORE_THE_SESSION = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
SESSION_START = datetime(2026, 4, 15, 14, 0, tzinfo=UTC)
SESSION_END = datetime(2026, 4, 15, 14, 50, tzinfo=UTC)


class FailingSender:
    """A mail backend that can deliver, until it is asked to."""

    can_deliver = True

    def send(self, message: OutboundEmail) -> None:
        raise RuntimeError("mail server said no")


def _facts(*, sends: bool = True, portal: str | None = PORTAL) -> Any:
    def read(practice_id: str | None) -> PracticeFacts | None:
        read.asked.append(practice_id)  # type: ignore[attr-defined]  # test-only stash
        return PracticeFacts(sends_link=sends, name=PRACTICE_NAME, portal_url=portal)

    read.asked = []  # type: ignore[attr-defined]  # test-only stash
    return read


def _use(target: Any, notifier: ClientVideoLinkNotifier) -> None:
    target.dependency_overrides[get_client_video_link_notifier] = lambda: notifier


def _at_clock(
    sender: Any, facts: Any = None, *, now: datetime = BEFORE_THE_SESSION
) -> ClientVideoLinkNotifier:
    """A notifier whose clock reads *now*, for the clinician routes' fixed dates."""
    return ClientVideoLinkNotifier(sender=sender, practice_facts=facts or _facts(), now=lambda: now)


# ------------------------------------------------------------- the message


def _appointment(**overrides: Any) -> Appointment:
    fields: dict[str, Any] = {
        "id": "appt-1",
        "user_id": "clinician-1",
        "patient_id": "patient-1",
        "title": "Intake for Jane Roe",
        "start_at": datetime(2026, 11, 18, 19, 0, tzinfo=UTC),
        "end_at": datetime(2026, 11, 18, 19, 50, tzinfo=UTC),
        "duration_minutes": 50,
        "status": AppointmentStatus.CONFIRMED,
        "session_type": "Grief counseling",
        "notes": "Follow up on panic attacks",
        "video_link": DOXY_ROOM,
        "place_of_service": "02",
    }
    fields.update(overrides)
    return Appointment(**fields)


class TestTheMessage:
    def test_it_says_who_when_and_where_to_join(self) -> None:
        message = compose(
            _appointment(),
            to=CLIENT_EMAIL,
            link=DOXY_SENT,
            practice=PracticeFacts(sends_link=True, name=PRACTICE_NAME, portal_url=PORTAL),
            tz=EASTERN,
            changed=False,
        )

        assert message.subject == f"Your video session with {PRACTICE_NAME}"
        assert message.text == (
            f"Your video session with {PRACTICE_NAME} is on "
            "Wednesday, November 18 at 2:00 PM EST.\n\n"
            f"Join here: {DOXY_SENT}\n\n"
            f"You can also join from your portal when it's time: {PORTAL}"
        )
        assert message.kind == KIND_BOOKED
        assert message.from_name == PRACTICE_NAME

    def test_a_change_says_it_changed(self) -> None:
        message = compose(
            _appointment(),
            to=CLIENT_EMAIL,
            link=DOXY_SENT,
            practice=PracticeFacts(sends_link=True, name=PRACTICE_NAME),
            tz=EASTERN,
            changed=True,
        )

        assert message.subject == f"Your video session with {PRACTICE_NAME} has changed"
        assert message.text.startswith(
            f"Your video session with {PRACTICE_NAME} is now on Wednesday, November 18"
        )
        assert message.kind == KIND_CHANGED
        # No portal, no portal line.
        assert "portal" not in message.text

    def test_the_time_is_the_practice_s_own(self) -> None:
        start = datetime(2026, 7, 1, 13, 5, tzinfo=UTC)

        assert describe_when(start, EASTERN) == "Wednesday, July 1 at 9:05 AM EDT"
        assert describe_when(start, ZoneInfo("America/Los_Angeles")) == (
            "Wednesday, July 1 at 6:05 AM PDT"
        )


# ------------------------------------------------- the clinician's diary


@pytest.fixture
def clinician(write_client: TestClient, mock_user_id: str) -> tuple[TestClient, Any]:
    """The clinician routes, with a client who has an email and a mail that records."""
    patients = InMemoryPatientRepository()
    patients.create(
        Patient(
            id="patient-1",
            first_name="Jane",
            last_name="Roe",
            email=CLIENT_EMAIL,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        ),
        mock_user_id,
    )
    sent = InMemoryEmailSender()
    facts = _facts()
    appointments = InMemoryAppointmentRepository()
    app.dependency_overrides[get_scheduling_service] = lambda: SchedulingService(appointments)
    app.dependency_overrides[get_patient_repository] = lambda: patients
    app.dependency_overrides[get_owner_timezone] = lambda: EASTERN
    _use(app, _at_clock(sent, facts))
    write_client.sent = sent  # type: ignore[attr-defined]  # test-only stash
    write_client.facts = facts  # type: ignore[attr-defined]  # test-only stash
    write_client.patients = patients  # type: ignore[attr-defined]  # test-only stash
    write_client.appointments = appointments  # type: ignore[attr-defined]  # test-only stash
    return write_client, sent


def _book(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload = _create_payload(place_of_service="02", video_link=DOXY_ROOM)
    payload.update(overrides)
    response = client.post("/api/appointments", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _edit(client: TestClient, appointment_id: str, **fields: Any) -> dict[str, Any]:
    response = client.patch(f"/api/appointments/{appointment_id}", json=fields)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestTheClinicianBooks:
    def test_a_video_booking_sends_the_client_one_message_with_the_link(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician

        _book(client)

        [message] = sent.sent
        assert message.to == CLIENT_EMAIL
        assert message.kind == KIND_BOOKED
        assert f"Join here: {DOXY_SENT}" in message.text
        # In the practice's zone: 14:00Z is 10:00 in New York in April.
        assert "Wednesday, April 15 at 10:00 AM EDT" in message.text
        # The practice the clinician is signed in to is the one asked.
        assert client.facts.asked == ["test-practice"]  # type: ignore[attr-defined]

    def test_the_message_carries_no_name_reason_or_chart(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician

        _book(client, title="Intake for Jane Roe", notes="Follow up on panic attacks")

        [message] = sent.sent
        for leaked in ("Jane", "Roe", "Intake", "panic", "username"):
            assert leaked not in message.subject
            assert leaked not in message.text

    def test_an_unchanged_re_save_sends_nothing(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        booked = _book(client)

        _edit(client, booked["id"], notes="Bring the intake form")
        _edit(client, booked["id"], video_link=DOXY_ROOM)

        assert len(sent.sent) == 1

    def test_a_new_time_sends_one_update_with_the_link(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        booked = _book(client)

        _edit(client, booked["id"], start_at="2026-04-16T15:00:00Z", end_at="2026-04-16T15:50:00Z")
        _edit(client, booked["id"], notes="saved again")

        assert [m.kind for m in sent.sent] == [KIND_BOOKED, KIND_CHANGED]
        update = sent.sent[1]
        assert "is now on Thursday, April 16 at 11:00 AM EDT" in update.text
        assert f"Join here: {DOXY_SENT}" in update.text

    def test_a_new_link_sends_one_update_with_the_new_link(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        booked = _book(client)

        _edit(client, booked["id"], video_link=NEW_ROOM)

        assert [m.kind for m in sent.sent] == [KIND_BOOKED, KIND_CHANGED]
        assert f"Join here: {NEW_ROOM}" in sent.sent[1].text

    def test_a_cancellation_sends_nothing_new(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        booked = _book(client)

        response = client.delete(f"/api/appointments/{booked['id']}")

        assert response.status_code == 200, response.text
        assert len(sent.sent) == 1

    def test_agreeing_to_a_request_sends_the_link(
        self, clinician: tuple[TestClient, InMemoryEmailSender], mock_user_id: str
    ) -> None:
        """A client's request with a standing room: the link goes when it is agreed to."""
        client, sent = clinician
        request = client.appointments.create(  # type: ignore[attr-defined]
            _appointment(user_id=mock_user_id, status=AppointmentStatus.PENDING)
        )

        _edit(client, request.id, status="confirmed")

        [message] = sent.sent
        assert message.kind == KIND_BOOKED


class TestNothingIsSent:
    def test_for_a_request_not_yet_agreed_to(
        self, clinician: tuple[TestClient, InMemoryEmailSender], mock_user_id: str
    ) -> None:
        """A request with a standing room, edited but not agreed to, has nothing to send."""
        client, sent = clinician
        request = client.appointments.create(  # type: ignore[attr-defined]
            _appointment(user_id=mock_user_id, status=AppointmentStatus.PENDING)
        )

        _edit(client, request.id, notes="Asked about evenings")

        assert sent.sent == []

    def test_when_the_practice_has_turned_it_off(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        _use(app, _at_clock(sent, _facts(sends=False)))

        _book(client)

        assert sent.sent == []

    def test_for_an_in_person_visit(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician

        _book(client, place_of_service="11")

        assert sent.sent == []

    def test_for_a_client_with_no_email(
        self, clinician: tuple[TestClient, InMemoryEmailSender], mock_user_id: str
    ) -> None:
        client, sent = clinician
        patient = client.patients.get("patient-1", mock_user_id)  # type: ignore[attr-defined]
        patient.email = None
        client.patients.update(patient)  # type: ignore[attr-defined]

        _book(client)

        assert sent.sent == []

    def test_when_the_deployment_has_no_mail_and_nothing_is_read(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, _sent = clinician
        facts = _facts()
        _use(app, _at_clock(NoneEmailSender(), facts))

        _book(client)

        assert facts.asked == []


class TestASessionAlreadyOver:
    """A finished session stays confirmed; nothing about it may mail the client."""

    def test_booking_it_after_the_fact_sends_nothing_and_reads_nothing(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        facts = _facts()
        _use(app, _at_clock(sent, facts, now=SESSION_END + timedelta(days=3)))

        _book(client)

        assert sent.sent == []
        assert facts.asked == []

    def test_editing_it_afterwards_sends_nothing(
        self, clinician: tuple[TestClient, InMemoryEmailSender], mock_user_id: str
    ) -> None:
        """Tom's probe: a past video session with no digest, then a late note."""
        client, sent = clinician
        past = client.appointments.create(  # type: ignore[attr-defined]
            _appointment(user_id=mock_user_id, start_at=SESSION_START, end_at=SESSION_END)
        )
        _use(app, _at_clock(sent, now=SESSION_END + timedelta(days=3)))

        _edit(client, past.id, notes="late note after the session")
        _edit(client, past.id, start_at="2026-04-15T15:00:00Z", end_at="2026-04-15T15:50:00Z")

        assert sent.sent == []

    def test_the_moment_it_ends_it_is_over(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        _use(app, _at_clock(sent, now=SESSION_END))

        _book(client)

        assert sent.sent == []

    def test_a_session_under_way_still_sends_the_link(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        """Booked ten minutes in: the link is still the way in."""
        client, sent = clinician
        _use(app, _at_clock(sent, now=SESSION_START + timedelta(minutes=10)))

        _book(client)

        [message] = sent.sent
        assert message.kind == KIND_BOOKED


class TestAnAppointmentFromBeforeThisMessage:
    def test_its_first_edit_while_still_ahead_sends_the_booked_message(
        self, clinician: tuple[TestClient, InMemoryEmailSender], mock_user_id: str
    ) -> None:
        """No digest yet: the client is sent the message they never had, once."""
        client, sent = clinician
        ahead = client.appointments.create(  # type: ignore[attr-defined]
            _appointment(user_id=mock_user_id, start_at=SESSION_START, end_at=SESSION_END)
        )

        _edit(client, ahead.id, notes="Bring the intake form")
        _edit(client, ahead.id, notes="saved again")

        [message] = sent.sent
        assert message.kind == KIND_BOOKED


class TestAFailedSend:
    def test_keeps_the_booking_and_the_next_save_sends(
        self, clinician: tuple[TestClient, InMemoryEmailSender]
    ) -> None:
        client, sent = clinician
        _use(app, _at_clock(FailingSender()))

        booked = _book(client)
        _use(app, _at_clock(sent))
        _edit(client, booked["id"], notes="saved again")

        # The failed send recorded nothing, so the re-save is a first message.
        [message] = sent.sent
        assert message.kind == KIND_BOOKED


# --------------------------------------------------- a client's booking


@pytest.fixture(autouse=True)
def _eastern(monkeypatch: pytest.MonkeyPatch) -> None:
    """The practice's zone, without the user repository the public fakes leave out."""
    monkeypatch.setattr(scheduling, "_owner_timezone", lambda _repo, _user_id: EASTERN)


@pytest.fixture(params=sorted(PATHS))
def client_books(request: pytest.FixtureRequest) -> Any:
    return PATHS[request.param]


class TestAClientsBookingIsConfirmed:
    def test_a_video_booking_sends_one_message_with_the_link(
        self,
        client_books: Any,
        link_repo: InMemoryBookingLinkRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        real_link = room_tests._link

        def in_a_practice(**kw: Any) -> Any:
            link = real_link(**kw)
            link.practice_id = "practice-1"
            return link

        monkeypatch.setattr(room_tests, "_link", in_a_practice)
        client, _rooms = _client(link_repo, monkeypatch, place="02", provider=_zoom())
        sent = InMemoryEmailSender()
        facts = _facts()
        _use(client.app, ClientVideoLinkNotifier(sender=sent, practice_facts=facts))

        appt = client_books(client, link_repo)

        [message] = sent.sent
        assert message.to == CLIENT_EMAIL
        assert "Join here: https://z.test/81" in message.text
        assert "Jane" not in message.text
        assert facts.asked == ["practice-1"]
        assert appt.client_link_notice is not None

    def test_an_in_person_booking_sends_nothing(
        self,
        client_books: Any,
        link_repo: InMemoryBookingLinkRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, _rooms = _client(link_repo, monkeypatch, place="11", provider=_zoom())
        sent = InMemoryEmailSender()
        _use(client.app, ClientVideoLinkNotifier(sender=sent, practice_facts=_facts()))

        client_books(client, link_repo)

        assert sent.sent == []


# ---------------------------------------------------- the late Meet read


class TestTheLateMeetRead:
    def test_the_link_it_fills_is_sent_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        repo = InMemoryAppointmentRepository()
        service = SchedulingService(repo)
        appt = repo.create(_appointment(provider=GOOGLE_MEET, video_link=None, google_event_id="e"))
        calendar = SlowMeetCalendar(None, later=MEET_LINK)
        calendar.following_up = True

        filled = _fill_meet_link(service, calendar, appt.user_id, appt.id, "e")  # type: ignore[arg-type]
        assert filled is not None
        assert filled.video_link == MEET_LINK
        # Nothing to fill a second time: the link is on the row now.
        assert _fill_meet_link(service, calendar, appt.user_id, appt.id, "e") is None  # type: ignore[arg-type]

    def test_the_follow_up_tells_the_client_what_it_filled(self) -> None:
        repo = InMemoryAppointmentRepository()
        service = SchedulingService(repo)
        appt = repo.create(_appointment(provider=GOOGLE_MEET, video_link=None, google_event_id="e"))
        calendar = SlowMeetCalendar(None, later=MEET_LINK)
        told: list[Appointment] = []

        class Spy(InlineFollowUp):
            def tell_client(self, service: SchedulingService, appt: Appointment) -> None:
                told.append(appt)

        Spy(service, calendar).run(appt.user_id, appt.id, "e")

        assert [a.video_link for a in told] == [MEET_LINK]

    def test_telling_the_client_sends_the_link_in_the_follow_up_s_practice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = InMemoryAppointmentRepository()
        service = SchedulingService(repo)
        # On the real clock, since the follow-up builds its own notifier: a
        # session tomorrow, whenever this runs.
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        appt = repo.create(
            _appointment(
                provider=GOOGLE_MEET,
                video_link=MEET_LINK,
                start_at=tomorrow,
                end_at=tomorrow + timedelta(minutes=50),
            )
        )
        patients = InMemoryPatientRepository()
        patients.create(
            Patient(
                id=appt.patient_id,
                first_name="Jane",
                last_name="Roe",
                email=CLIENT_EMAIL,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            ),
            appt.user_id,
        )
        sent = InMemoryEmailSender()
        schemas: list[str | None] = []
        monkeypatch.setattr(scheduling, "get_email_sender", lambda: sent)
        monkeypatch.setattr(scheduling, "_patient_repo_factory", lambda: patients)
        monkeypatch.setattr(scheduling, "get_user_repository", lambda: None)
        monkeypatch.setattr(
            scheduling, "practice_id_for_schema", lambda schema: schemas.append(schema) or "p-1"
        )
        monkeypatch.setattr(scheduling, "read_practice_facts", _facts())

        scheduling.MeetLinkFollowUp("practice_late").tell_client(service, appt)

        [message] = sent.sent
        assert f"Join here: {MEET_LINK}" in message.text
        assert schemas == ["practice_late"]
        assert repo.get(appt.id, appt.user_id).client_link_notice is not None  # type: ignore[union-attr]
