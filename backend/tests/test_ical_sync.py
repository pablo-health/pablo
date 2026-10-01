# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for iCal calendar sync service."""

from __future__ import annotations

import base64
import io
import os
import zipfile
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
from app.models.patient import Patient
from app.patients.identifiers import clinician_scope, identifier_digest
from app.patients.matching import remember_match, remember_not_a_client
from app.repositories.external_calendar_event import InMemoryExternalCalendarEventRepository
from app.repositories.ical_sync_config import ICalSyncConfig
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.patient_source_mapping import (
    InMemoryPatientSourceMappingRepository,
    PatientSourceMapping,
)
from app.scheduling_engine.models.appointment import AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.ical_sync_service import (
    FEED_URL_REFUSED,
    FeedUrlRefusedError,
    ICalSyncService,
    ParsedEvent,
)
from app.services.token_encryption import decrypt_tokens, encrypt_tokens
from app.settings import get_settings
from app.utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Generator

    from app.patients.matching import MatchContext

# Real iCal feed data from SimplePractice test account
SP_ICAL_DATA = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:icalendar-ruby
CALSCALE:GREGORIAN
X-WR-CALNAME:SimplePractice
X-PUBLISHED-TTL:PT10M
X-WR-TIMEZONE:America/New_York
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
DTSTART:20070311T030000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
TZNAME:EDT
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20061029T010000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYDAY=-1SU;BYMONTH=10
TZNAME:EST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTAMP:20260325T005244Z
UID:3415461692
DTSTART;TZID=America/New_York:20260318T140000
DTEND;TZID=America/New_York:20260318T150000
LOCATION:
SUMMARY:J.A. Appointment
END:VEVENT
BEGIN:VEVENT
DTSTAMP:20260325T005244Z
UID:3426439378
DTSTART;TZID=America/New_York:20260323T200000
DTEND;TZID=America/New_York:20260323T205000
SUMMARY:P.B. Appointment
URL;VALUE=URI:https://video.simplepractice.com/appt-485bc95d4f126fadb091e02f240ea244
END:VEVENT
END:VCALENDAR"""

# The same feed with the calendar sync set to show full names, which is the
# only setting under which a feed books on its own: initials never identify one
# client. Captures of both settings are under ``fixtures/simplepractice_feed``.
SP_NAMES_ICAL_DATA = SP_ICAL_DATA.replace("J.A. Appointment", "Jane Adams Appointment").replace(
    "P.B. Appointment", "Pablo Bear Appointment"
)

# Real iCal feed data from Sessions Health test account
SH_ICAL_DATA = """\
BEGIN:VCALENDAR
VERSION:2.0
PRODID:Sessions\\, Inc.
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:Kurt Niemi (Sessions Health)
BEGIN:VTIMEZONE
TZID:America/New_York
BEGIN:DAYLIGHT
DTSTART:20260308T030000
TZOFFSETFROM:-0500
TZOFFSETTO:-0400
RRULE:FREQ=YEARLY;BYDAY=2SU;BYMONTH=3
TZNAME:EDT
END:DAYLIGHT
BEGIN:STANDARD
DTSTART:20261101T010000
TZOFFSETFROM:-0400
TZOFFSETTO:-0500
RRULE:FREQ=YEARLY;BYDAY=1SU;BYMONTH=11
TZNAME:EST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
DTSTAMP:20260325T011548Z
UID:21420944-260316@app.sessionshealth.com
DTSTART;TZID=America/New_York:20260316T190000
DTEND;TZID=America/New_York:20260316T200000
CLASS:PUBLIC
SUMMARY:SH00001
URL;VALUE=URI:https://app.sessionshealth.com/events/21420944-260316
END:VEVENT
BEGIN:VEVENT
DTSTAMP:20260325T011548Z
UID:21629232-260325@app.sessionshealth.com
DTSTART;TZID=America/New_York:20260325T130000
DTEND;TZID=America/New_York:20260325T133000
CLASS:PUBLIC
SUMMARY:SH00002
URL;VALUE=URI:https://app.sessionshealth.com/events/21629232-260325
END:VEVENT
END:VCALENDAR"""


def _now() -> datetime:
    return utc_now()


def _make_patient(patient_id: str, first: str, last: str, user_id: str = "user1") -> Patient:
    now = _now()
    return Patient(
        id=patient_id,
        first_name=first,
        last_name=last,
        created_at=now,
        updated_at=now,
    )


class InMemoryICalSyncConfigRepo:
    """In-memory config repo for tests."""

    def __init__(self) -> None:
        self._configs: dict[str, ICalSyncConfig] = {}

    def get(self, user_id: str, ehr_system: str) -> ICalSyncConfig | None:
        return self._configs.get(f"{user_id}_{ehr_system}")

    def list_by_user(self, user_id: str) -> list[ICalSyncConfig]:
        return [c for c in self._configs.values() if c.user_id == user_id]

    def save(self, config: ICalSyncConfig) -> None:
        self._configs[config.doc_id] = config

    def delete(self, user_id: str, ehr_system: str) -> bool:
        key = f"{user_id}_{ehr_system}"
        if key in self._configs:
            del self._configs[key]
            return True
        return False

    def update_sync_status(
        self,
        user_id: str,
        ehr_system: str,
        *,
        error: str | None = None,
        title_style: str | None = None,
    ) -> None:
        key = f"{user_id}_{ehr_system}"
        if key in self._configs:
            self._configs[key].last_synced_at = _now()
            self._configs[key].last_sync_error = error
            if title_style is not None:
                self._configs[key].title_style = title_style


@pytest.fixture
def _encryption_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Set up a test encryption key.

    The key is read through the cached ``get_settings()``, so setting the
    environment alone only works when nothing has cached settings since the
    last clear — which made these tests depend on which file ran before them.
    """
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", key)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def service(_encryption_key: Any):
    """Create an ICalSyncService with in-memory repos."""
    return ICalSyncService(
        config_repo=InMemoryICalSyncConfigRepo(),  # type: ignore[arg-type]
        appointment_repo=InMemoryAppointmentRepository(),
        patient_repo=InMemoryPatientRepository(),
        mapping_repo=InMemoryPatientSourceMappingRepository(),
        external_events=InMemoryExternalCalendarEventRepository(),
    )


class TestICalParsing:
    """Tests for iCal feed parsing."""

    def test_parse_simplepractice_events(self, service: ICalSyncService):
        events = service._parse_events(SP_ICAL_DATA)
        assert len(events) == 2

        # First event
        e1 = next(e for e in events if e.uid == "3415461692")
        assert e1.summary == "J.A. Appointment"
        assert e1.duration_minutes == 60
        assert e1.url is None

        # Second event with video link
        e2 = next(e for e in events if e.uid == "3426439378")
        assert e2.summary == "P.B. Appointment"
        assert e2.duration_minutes == 50
        assert e2.url is not None
        assert urlparse(e2.url).hostname == "video.simplepractice.com"

    def test_parse_sessions_health_events(self, service: ICalSyncService):
        events = service._parse_events(SH_ICAL_DATA)
        assert len(events) == 2

        e1 = next(e for e in events if e.uid == "21420944-260316@app.sessionshealth.com")
        assert e1.summary == "SH00001"
        assert e1.duration_minutes == 60
        assert "sessionshealth.com/events" in (e1.url or "")

        e2 = next(e for e in events if e.uid == "21629232-260325@app.sessionshealth.com")
        assert e2.summary == "SH00002"
        assert e2.duration_minutes == 30

    def test_timezone_conversion_to_utc(self, service: ICalSyncService):
        """EDT events should be converted to UTC (add 4 hours)."""
        events = service._parse_events(SP_ICAL_DATA)
        e = next(e for e in events if e.uid == "3415461692")
        # 2:00 PM EDT = 6:00 PM UTC
        assert e.start_at.hour == 18
        assert e.start_at.minute == 0
        assert e.end_at.hour == 19
        assert e.end_at.minute == 0

    def test_an_event_without_an_end_or_an_all_day_one_is_left_out(self, service: ICalSyncService):
        """Neither is a session: no end means no duration, and a day is not a slot."""
        extra = (
            "BEGIN:VEVENT\nUID:no-end\n"
            "DTSTART;TZID=America/New_York:20260318T140000\n"
            "SUMMARY:J.A. Appointment\nEND:VEVENT\n"
            "BEGIN:VEVENT\nUID:all-day\n"
            "DTSTART;VALUE=DATE:20260318\nDTEND;VALUE=DATE:20260319\n"
            "SUMMARY:Out of office\nEND:VEVENT\n"
            "END:VCALENDAR"
        )

        events = service._parse_events(SP_ICAL_DATA.replace("END:VCALENDAR", extra))

        assert sorted(e.uid for e in events) == ["3415461692", "3426439378"]


class TestClientMatching:
    """Tests for client identifier extraction and matching."""

    @pytest.mark.parametrize(
        ("ehr_system", "summary", "identifier"),
        [
            ("simplepractice", "J.A. Appointment", "J.A."),
            ("simplepractice", "Jane Adams Appointment", "Jane Adams"),
            ("sessions_health", "SH00001", "SH00001"),
            # Sessions Health pads nothing, but a code typed with a stray
            # space must still be the code it was remembered under.
            ("sessions_health", " SH00001 ", "SH00001"),
        ],
    )
    def test_the_client_identifier_is_read_from_the_title(
        self, service: ICalSyncService, ehr_system: str, summary: str, identifier: str
    ):
        assert service._extract_client_identifier(ehr_system, summary) == identifier

    def test_unique_initials_are_offered_never_booked(self, service: ICalSyncService):
        """ "J.A." fits Jane Adams alone today; tomorrow it may fit someone new."""
        patients = [
            _make_patient("p1", "Jane", "Adams"),
            _make_patient("p2", "Bob", "Smith"),
        ]
        ctx = _context(service, patients)
        assert service._match("simplepractice", "J.A.", ctx).patient_id == "p1"
        assert _unattended(service, "simplepractice", "J.A. Appointment", ctx) is None

    def test_a_full_name_one_chart_bears_books(self, service: ICalSyncService):
        patients = [
            _make_patient("p1", "Jane", "Adams"),
            _make_patient("p2", "Bob", "Smith"),
        ]
        ctx = _context(service, patients)
        assert _unattended(service, "simplepractice", "Jane Adams Appointment", ctx) == "p1"

    def test_a_feeds_own_client_code_books_once_answered(self, service: ICalSyncService):
        service._mapping_repo.save(
            _answered("sessions_health", "SH00001", "patient-abc"),
        )
        ctx = _context(service, [_make_patient("patient-abc", "Pablo", "Bear")])
        assert _unattended(service, "sessions_health", "SH00001", ctx) == "patient-abc"

    def test_two_clients_sharing_initials_are_asked_every_time(self, service: ICalSyncService):
        """John Adams and James Andersson are both "J.A.": the answer is the next pre-fill."""
        ctx = _context(
            service,
            [_make_patient("john", "John", "Adams"), _make_patient("james", "James", "Andersson")],
        )
        first = service._match("simplepractice", "J.A.", ctx)
        assert first.patient_id is None
        assert sorted(first.possible_ids) == ["james", "john"]

        # The clinician picks John; that is remembered for the feed's "J.A.".
        remember_match("simplepractice", "J.A.", "john", ctx, scope=clinician_scope("user1"))

        ctx = service._match_context("user1")
        later = service._match("simplepractice", "J.A.", ctx)
        assert (later.patient_id, later.evidence) == ("john", "remembered")
        # Remembered, and still not booked: initials don't say which J.A. this is.
        assert _unattended(service, "simplepractice", "J.A. Appointment", ctx) is None

    def test_a_colleagues_client_sharing_initials_is_not_matched(self, service: ICalSyncService):
        """Initials are weak evidence: only the clinician's own charts count."""
        service._patient_repo.create(_make_patient("theirs", "Jane", "Adams"), "colleague")
        ctx = service._match_context("user1")

        assert service._match("simplepractice", "J.A.", ctx).patient_id is None
        assert _unattended(service, "simplepractice", "J.A. Appointment", ctx) is None

    def test_a_remembered_colleagues_client_is_never_booked_from_a_feed(
        self, service: ICalSyncService
    ):
        """Matched on strong evidence, but only the clinician's own chart gets the booking."""
        service._patient_repo.create(_make_patient("theirs", "Jane", "Adams"), "colleague")
        service._mapping_repo.save(_answered("sessions_health", "SH00001", "theirs"))
        ctx = service._match_context("user1")

        assert service._match("sessions_health", "SH00001", ctx).patient_id == "theirs"
        assert _unattended(service, "sessions_health", "SH00001", ctx) is None


def _answered(source: str, identifier: str, patient_id: str) -> PatientSourceMapping:
    """A feed identifier user1 answered earlier, as stored: their own answer."""
    return PatientSourceMapping(
        clinician_scope("user1"), source, identifier_digest(identifier), patient_id, "user1"
    )


def _unattended(service: ICalSyncService, ehr: str, summary: str, ctx: MatchContext) -> str | None:
    """The chart a feed event with this title books to without asking, if any."""
    start = _now()
    event = ParsedEvent(
        uid="uid-1", summary=summary, start_at=start, end_at=start, duration_minutes=60
    )
    return service._outside.unattended(ICalSyncService._row("user1", ehr, event), ctx)


def _context(service: ICalSyncService, patients: list[Patient]) -> MatchContext:
    for patient in patients:
        service._patient_repo.create(patient, "user1")
    return service._match_context("user1")


class TestSyncDiff:
    """Tests for the sync create/update/delete logic."""

    @pytest.fixture
    def sync_service(self, _encryption_key: Any):
        """Service with pre-populated config."""
        config_repo = InMemoryICalSyncConfigRepo()
        appt_repo = InMemoryAppointmentRepository()
        patient_repo = InMemoryPatientRepository()
        mapping_repo = InMemoryPatientSourceMappingRepository()

        encrypted = encrypt_tokens({"feed_url": "https://secure.simplepractice.com/ical/test"})
        config = ICalSyncConfig(
            user_id="user1",
            ehr_system="simplepractice",
            encrypted_feed_url=encrypted,
            connected_at=_now(),
        )
        config_repo.save(config)

        svc = ICalSyncService(
            config_repo=config_repo,  # type: ignore[arg-type]
            appointment_repo=appt_repo,
            patient_repo=patient_repo,
            mapping_repo=mapping_repo,
            external_events=InMemoryExternalCalendarEventRepository(),
        )
        # The feed's two clients are on the caseload, so its events are
        # sessions; TestUnmatchedFeedEvents covers a feed nobody matches.
        patient_repo.create(_make_patient("p-ja", "Jane", "Adams"), "user1")
        patient_repo.create(_make_patient("p-pb", "Pablo", "Bear"), "user1")
        return svc

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_initial_sync_creates_appointments(
        self, mock_fetch: MagicMock, sync_service: ICalSyncService
    ):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        results = sync_service.sync("user1", "simplepractice")

        assert len(results) == 1
        result = results[0]
        assert result.created == 2
        assert result.updated == 0
        assert result.deleted == 0

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_an_identifier_remembered_as_not_a_client_is_skipped(
        self, mock_fetch: MagicMock, sync_service: ICalSyncService
    ):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        remember_not_a_client(
            "simplepractice",
            "Jane Adams",
            sync_service._match_context("user1"),
            scope=clinician_scope("user1"),
        )

        [result] = sync_service.sync("user1", "simplepractice")

        assert result.created == 1
        assert result.unmatched_events == []
        [appointment] = sync_service._appt_repo.list_by_ical_source(
            "user1", sync_service._config_repo.list_by_user("user1")[0].ehr_system
        )
        assert appointment.patient_id == "p-pb"

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_a_remembered_client_since_deleted_is_asked_about_not_reassigned(
        self, mock_fetch: MagicMock, sync_service: ICalSyncService
    ):
        """ "J.A." meant John Adams. With John deleted, Jane Anderson must not inherit it."""
        mock_fetch.return_value = SP_ICAL_DATA
        patients = sync_service._patient_repo
        patients.create(_make_patient("john", "John", "Adams"), "user1")
        patients.create(_make_patient("jane", "Jane", "Anderson"), "user1")
        sync_service.resolve_client("user1", "simplepractice", "J.A.", "john")
        patients.delete("john", "user1")

        [result] = sync_service.sync("user1", "simplepractice")

        [unmatched] = [e for e in result.unmatched_events if e["client_identifier"] == "J.A."]
        # Enough to open the appointment where it was booked.
        assert unmatched == {
            "ical_uid": "3415461692",
            "client_identifier": "J.A.",
            "start_at": "2026-03-18T18:00:00+00:00",
            "ehr_appointment_url": "https://secure.simplepractice.com/appointments/3415461692",
        }

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_second_sync_no_changes(self, mock_fetch: MagicMock, sync_service: ICalSyncService):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        sync_service.sync("user1", "simplepractice")

        # Second sync — no changes
        results = sync_service.sync("user1", "simplepractice")
        result = results[0]
        assert result.created == 0
        assert result.updated == 0
        assert result.unchanged == 2

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_deleted_event_soft_deletes_appointment(
        self, mock_fetch: MagicMock, sync_service: ICalSyncService
    ):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        sync_service.sync("user1", "simplepractice")

        # Second sync with one event removed
        mock_fetch.return_value = _WITHOUT_PABLO
        results = sync_service.sync("user1", "simplepractice")
        result = results[0]
        assert result.deleted == 1
        assert result.unchanged == 1
        # The one that left is cancelled; the one still in the feed is not.
        by_uid = _by_uid(sync_service)
        gone, kept = by_uid["3426439378"], by_uid["3415461692"]
        assert (gone.status, gone.ical_sync_status) == (AppointmentStatus.CANCELLED, "deleted")
        assert gone.updated_at is not None
        assert (kept.status, kept.ical_sync_status) == (AppointmentStatus.CONFIRMED, "synced")

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_a_moved_event_moves_its_appointment(
        self, mock_fetch: MagicMock, sync_service: ICalSyncService
    ):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        sync_service.sync("user1", "simplepractice")
        before = _by_uid(sync_service)["3415461692"]

        # Jane's session, an hour and a half later and fifteen minutes longer.
        mock_fetch.return_value = SP_NAMES_ICAL_DATA.replace(
            "DTSTART;TZID=America/New_York:20260318T140000\n"
            "DTEND;TZID=America/New_York:20260318T150000\n",
            "DTSTART;TZID=America/New_York:20260318T153000\n"
            "DTEND;TZID=America/New_York:20260318T164500\n",
        )
        [result] = sync_service.sync("user1", "simplepractice")

        assert (result.created, result.updated, result.unchanged, result.deleted) == (0, 1, 1, 0)
        moved = _by_uid(sync_service)["3415461692"]
        assert moved.start_at == before.start_at + timedelta(hours=1, minutes=30)
        assert moved.end_at == before.end_at + timedelta(hours=1, minutes=45)
        assert moved.duration_minutes == 75
        assert moved.updated_at is not None

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_an_event_that_comes_back_restores_its_appointment(
        self, mock_fetch: MagicMock, sync_service: ICalSyncService
    ):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        sync_service.sync("user1", "simplepractice")
        mock_fetch.return_value = _WITHOUT_PABLO
        sync_service.sync("user1", "simplepractice")
        assert _by_uid(sync_service)["3426439378"].status == AppointmentStatus.CANCELLED

        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        [result] = sync_service.sync("user1", "simplepractice")

        assert (result.created, result.updated, result.unchanged, result.deleted) == (0, 1, 1, 0)
        restored = _by_uid(sync_service)["3426439378"]
        assert (restored.status, restored.ical_sync_status) == (
            AppointmentStatus.CONFIRMED,
            "synced",
        )

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_ehr_appointment_url_set(self, mock_fetch: MagicMock, sync_service: ICalSyncService):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        sync_service.sync("user1", "simplepractice")

        appts = sync_service._appt_repo.list_by_ical_source("user1", "simplepractice")
        sp_appt = next(a for a in appts if a.ical_uid == "3415461692")
        assert (
            sp_appt.ehr_appointment_url
            == "https://secure.simplepractice.com/appointments/3415461692"
        )

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_video_link_extracted(self, mock_fetch: MagicMock, sync_service: ICalSyncService):
        mock_fetch.return_value = SP_NAMES_ICAL_DATA
        sync_service.sync("user1", "simplepractice")

        appts = sync_service._appt_repo.list_by_ical_source("user1", "simplepractice")
        video_appt = next(a for a in appts if a.ical_uid == "3426439378")
        assert video_appt.video_link is not None
        assert urlparse(video_appt.video_link).hostname == "video.simplepractice.com"
        assert video_appt.video_platform == "simplepractice"
        plain = next(a for a in appts if a.ical_uid == "3415461692")
        assert (plain.video_link, plain.video_platform) == (None, None)


def _by_uid(service: ICalSyncService) -> dict[str, Any]:
    """The feed's appointments, by the feed's own id for each."""
    appointments = service._appt_repo.list_by_ical_source("user1", "simplepractice")
    return {a.ical_uid: a for a in appointments}


# The names feed with Pablo Bear's appointment gone from it.
_WITHOUT_PABLO = SP_NAMES_ICAL_DATA.replace(
    "BEGIN:VEVENT\nDTSTAMP:20260325T005244Z\n"
    "UID:3426439378\n"
    "DTSTART;TZID=America/New_York:20260323T200000\n"
    "DTEND;TZID=America/New_York:20260323T205000\n"
    "SUMMARY:Pablo Bear Appointment\n"
    "URL;VALUE=URI:https://video.simplepractice.com/appt-485bc95d4f126fadb091e02f240ea244\n"
    "END:VEVENT\n",
    "",
)


# A feed that names its clients in a way nothing matches until the clinician
# says who they are.
_PLAIN_FEED = "sessions_health"
_PLAIN_ICAL = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:feed-event-1
DTSTART:20990105T150000Z
DTEND:20990105T155000Z
SUMMARY:SH00007
END:VEVENT
BEGIN:VEVENT
UID:feed-event-2
DTSTART:20990112T150000Z
DTEND:20990112T155000Z
SUMMARY:SH00007
END:VEVENT
END:VCALENDAR"""


class TestUnmatchedFeedEvents:
    """A feed event nobody can match is held as a question, never an appointment."""

    @pytest.fixture
    def feed(self, _encryption_key: Any) -> ICalSyncService:
        config_repo = InMemoryICalSyncConfigRepo()
        config_repo.save(
            ICalSyncConfig(
                user_id="user1",
                ehr_system=_PLAIN_FEED,
                encrypted_feed_url=encrypt_tokens(
                    {"feed_url": "https://app.sessionshealth.com/calendars/test/calendar.ics"}
                ),
                connected_at=_now(),
            )
        )
        return ICalSyncService(
            config_repo=config_repo,  # type: ignore[arg-type]
            appointment_repo=InMemoryAppointmentRepository(),
            patient_repo=InMemoryPatientRepository(),
            mapping_repo=InMemoryPatientSourceMappingRepository(),
            external_events=InMemoryExternalCalendarEventRepository(),
        )

    @staticmethod
    def _open(feed: ICalSyncService) -> list[str]:
        return sorted(e.source_event_id for e in feed._outside._events.list_open("user1"))

    @patch.object(ICalSyncService, "_fetch_feed", return_value=_PLAIN_ICAL)
    def test_an_unmatched_event_is_held_not_booked(
        self, fetch: MagicMock, feed: ICalSyncService
    ) -> None:
        [result] = feed.sync("user1")

        assert result.created == 0
        assert feed._appt_repo.list_by_ical_source("user1", _PLAIN_FEED) == []
        assert self._open(feed) == ["feed-event-1", "feed-event-2"]
        assert {e["client_identifier"] for e in result.unmatched_events} == {"SH00007"}
        # Where and when, and the feed's own link where it gives one (here it doesn't).
        assert sorted(
            (e["ical_uid"], e["start_at"], e["ehr_appointment_url"])
            for e in result.unmatched_events
        ) == [
            ("feed-event-1", "2099-01-05T15:00:00+00:00", ""),
            ("feed-event-2", "2099-01-12T15:00:00+00:00", ""),
        ]

    @patch.object(ICalSyncService, "_fetch_feed", return_value=_PLAIN_ICAL)
    def test_no_appointment_is_ever_written_without_a_patient(
        self, fetch: MagicMock, feed: ICalSyncService
    ) -> None:
        feed.sync("user1")
        feed.sync("user1")

        assert all(a.patient_id for a in feed._appt_repo._appointments.values())

    @patch.object(ICalSyncService, "_fetch_feed", return_value=_PLAIN_ICAL)
    def test_resolving_the_client_books_the_held_events_and_remembers(
        self, fetch: MagicMock, feed: ICalSyncService
    ) -> None:
        feed._patient_repo.create(_make_patient("p7", "Seven", "Client"), "user1")
        feed.sync("user1")

        feed.resolve_client("user1", _PLAIN_FEED, "SH00007", "p7")

        booked = feed._appt_repo.list_by_ical_source("user1", _PLAIN_FEED)
        assert sorted(a.ical_uid or "" for a in booked) == ["feed-event-1", "feed-event-2"]
        assert {a.patient_id for a in booked} == {"p7"}
        assert self._open(feed) == []
        # The next read finds them as its own, and asks nothing.
        [result] = feed.sync("user1")
        assert (result.created, result.unmatched_events) == (0, [])

    @patch.object(ICalSyncService, "_fetch_feed", return_value=_PLAIN_ICAL)
    def test_an_event_that_leaves_the_feed_takes_its_question_with_it(
        self, fetch: MagicMock, feed: ICalSyncService
    ) -> None:
        feed.sync("user1")
        fetch.return_value = _PLAIN_ICAL.replace("UID:feed-event-2", "UID:feed-event-3")

        feed.sync("user1")

        assert self._open(feed) == ["feed-event-1", "feed-event-3"]


class TestUrlValidation:
    """Tests for feed URL validation."""

    def test_valid_sp_url(self, service: ICalSyncService):
        service._validate_feed_url(
            "simplepractice",
            "https://secure.simplepractice.com/ical/abc123",
        )

    @pytest.mark.parametrize("ehr_system", ["simplepractice", "sessions_health"])
    def test_another_host_is_refused(self, service: ICalSyncService, ehr_system: str):
        with pytest.raises(ValueError, match="hostname must be"):
            service._validate_feed_url(ehr_system, "https://evil.com/feed")

    def test_invalid_sp_url_http(self, service: ICalSyncService):
        with pytest.raises(ValueError, match="must use HTTPS"):
            service._validate_feed_url(
                "simplepractice", "http://secure.simplepractice.com/ical/abc"
            )

    def test_valid_sh_url(self, service: ICalSyncService):
        service._validate_feed_url(
            "sessions_health",
            "https://app.sessionshealth.com/calendars/123-abc/calendar.ics",
        )

    def test_unsupported_ehr(self, service: ICalSyncService):
        with pytest.raises(ValueError, match="Unsupported"):
            service._validate_feed_url("unknown_ehr", "https://example.com")

    def test_the_validated_form_is_scheme_host_and_path_only(self, service: ICalSyncService):
        assert (
            service._validate_feed_url(
                "simplepractice", "HTTPS://Secure.SimplePractice.com/ical/Ab1"
            )
            == "https://secure.simplepractice.com/ical/Ab1"
        )

    @pytest.mark.parametrize(
        ("feed_url", "refused_for"),
        [
            ("https://secure.simplepractice.com:8443/ical/feed.ics", "port"),
            ("https://u:p@secure.simplepractice.com/ical/feed.ics", "username or password"),
            ("https://secure.simplepractice.com/ical/feed.ics#f", "fragment"),
            ("https://secure.simplepractice.com/ical/../admin/feed.ics", "dot segments"),
            ("https://secure.simplepractice.com/ical/%2e%2e/admin/feed.ics", "dot segments"),
            ("https://secure.simplepractice.com/ical/./feed.ics", "dot segments"),
            ("https://secure.simplepractice.com/ical//feed.ics", "empty or dot"),
            ("https://secure.simplepractice.com/ical/", "empty or dot"),
        ],
    )
    def test_anything_beyond_a_plain_path_is_refused(
        self, service: ICalSyncService, feed_url: str, refused_for: str
    ):
        with pytest.raises(ValueError, match=refused_for):
            service._validate_feed_url("simplepractice", feed_url)


SP_FEED_URL = "https://secure.simplepractice.com/ical/abc123/feed.ics"


def _serving(ical: str) -> MagicMock:
    """A stand-in for ``urlopen`` whose response body is ``ical``."""
    opened = MagicMock()
    opened.__enter__.return_value.read.return_value = ical.encode("utf-8")
    return MagicMock(return_value=opened)


def _fetched(mock_urlopen: MagicMock) -> str:
    """The URL the one fetch was made to."""
    (request,), _ = mock_urlopen.call_args
    return request.full_url


class TestFeedOrigin:
    """Where a feed is read from: the provider, or the origin a deployment names.

    The end-to-end stack serves captured feeds from a stand-in, so a feed URL
    that has passed the allowlist is fetched from ``ICAL_FEED_BASE_URL``
    instead of the provider. The allowlist itself is untouched by the setting.
    """

    @pytest.fixture
    def _no_origin(self, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
        monkeypatch.delenv("ICAL_FEED_BASE_URL", raising=False)
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.fixture
    def _fake_origin(self, monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
        monkeypatch.setenv("ICAL_FEED_BASE_URL", "http://fake-ical:8082/")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    @pytest.mark.usefixtures("_no_origin")
    def test_unset_reads_the_provider(self, service: ICalSyncService):
        with patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)) as opened:
            service._fetch_feed(SP_FEED_URL)

        assert _fetched(opened) == SP_FEED_URL

    @pytest.mark.usefixtures("_fake_origin")
    def test_set_reads_the_same_path_from_that_origin(self, service: ICalSyncService):
        with patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)) as opened:
            body = service._fetch_feed(SP_FEED_URL)

        assert _fetched(opened) == "http://fake-ical:8082/ical/abc123/feed.ics"
        assert body == SP_ICAL_DATA

    @pytest.mark.usefixtures("_fake_origin", "_encryption_key")
    def test_connecting_reads_the_validated_path_from_that_origin(self, service: ICalSyncService):
        with patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)) as opened:
            result = service.configure("user1", "simplepractice", SP_FEED_URL)

        assert result.event_count == 2
        assert _fetched(opened) == "http://fake-ical:8082/ical/abc123/feed.ics"

    @pytest.mark.usefixtures("_no_origin", "_encryption_key")
    def test_connecting_stores_the_validated_url_and_reads_it_from_then_on(
        self, service: ICalSyncService
    ):
        typed = "HTTPS://Secure.SimplePractice.com/ical/abc123/feed.ics"
        with patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)):
            service.configure("user1", "simplepractice", typed)
        [config] = service._config_repo.list_by_user("user1")
        assert decrypt_tokens(config.encrypted_feed_url) == {"feed_url": SP_FEED_URL}

        with patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)) as opened:
            [result] = service.sync("user1", "simplepractice")

        assert result.errors == []
        assert _fetched(opened) == SP_FEED_URL

    @pytest.mark.usefixtures("_fake_origin", "_encryption_key")
    def test_a_stored_url_off_the_allowlist_is_refused_at_every_read(
        self, service: ICalSyncService
    ):
        # A row from before the validated form was stored: the URL as typed.
        service._config_repo.save(
            ICalSyncConfig(
                user_id="user1",
                ehr_system="simplepractice",
                encrypted_feed_url=encrypt_tokens(
                    {"feed_url": "https://secure.simplepractice.com/ical/../admin/feed.ics"}
                ),
                connected_at=_now(),
            )
        )
        with patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)) as opened:
            [result] = service.sync("user1", "simplepractice")

        opened.assert_not_called()
        assert result.errors == [FEED_URL_REFUSED]
        [config] = service._config_repo.list_by_user("user1")
        assert config.last_sync_error == FEED_URL_REFUSED

    @pytest.mark.usefixtures("_fake_origin")
    def test_the_allowlist_still_refuses_a_url_off_the_provider(self, service: ICalSyncService):
        with (
            patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)) as opened,
            pytest.raises(ValueError, match="hostname must be"),
        ):
            service.configure("user1", "simplepractice", "https://evil.example/ical/feed.ics")

        opened.assert_not_called()

    @pytest.mark.usefixtures("_fake_origin")
    def test_the_allowlist_still_refuses_a_path_off_the_feed(self, service: ICalSyncService):
        with (
            patch("app.services.ical_sync_service.urlopen", _serving(SP_ICAL_DATA)) as opened,
            pytest.raises(ValueError, match="path must start with"),
        ):
            service.configure(
                "user1", "simplepractice", "https://secure.simplepractice.com/admin/feed.ics"
            )

        opened.assert_not_called()


class TestCsvImport:
    """Tests for CSV/zip client import with auto-mapping."""

    def test_import_csv_creates_patients(self, service: ICalSyncService):
        csv_content = (
            "First Name,Last Name,Email,Birth Date,Phone Number,"
            "Street Address,City,State,ZIP Code,Active,Diagnosis,"
            "Assigned Practitioner,Payer Name,Member ID\n"
            "Pablo,Bear,,,,,,,,Y,,Kurt Niemi,,\n"
            "Lulu,Niemi,,,,,,,,Y,,Kurt Niemi,,\n"
        )
        result = service.import_clients(
            "user1", "sessions_health", csv_content.encode(), "clients.csv"
        )
        assert result.imported == 2
        assert result.skipped == 0

    def test_import_csv_skips_duplicates(self, service: ICalSyncService):
        # Pre-create a patient
        patient = _make_patient("existing", "Pablo", "Bear", "user1")
        service._patient_repo.create(patient, "user1")

        csv_content = (
            "First Name,Last Name,Email,Birth Date,Phone Number,"
            "Street Address,City,State,ZIP Code,Active,Diagnosis,"
            "Assigned Practitioner,Payer Name,Member ID\n"
            "Pablo,Bear,,,,,,,,Y,,Kurt Niemi,,\n"
            "Lulu,Niemi,,,,,,,,Y,,Kurt Niemi,,\n"
        )
        result = service.import_clients(
            "user1", "sessions_health", csv_content.encode(), "clients.csv"
        )
        assert result.imported == 1
        assert result.skipped == 1

    def test_import_creates_sh_mappings(self, service: ICalSyncService):
        csv_content = (
            "First Name,Last Name,Email,Birth Date,Phone Number,"
            "Street Address,City,State,ZIP Code,Active,Diagnosis,"
            "Assigned Practitioner,Payer Name,Member ID\n"
            "Pablo,Bear,,,,,,,,Y,,Kurt Niemi,,\n"
            "Lulu,Niemi,,,,,,,,Y,,Kurt Niemi,,\n"
        )
        result = service.import_clients(
            "user1", "sessions_health", csv_content.encode(), "clients.csv"
        )
        assert result.mappings_created == 2

        # Verify SH00001 maps to Pablo Bear, as the practice's answer
        stored = service._mapping_repo.list_by_source(clinician_scope("user1"), "sessions_health")
        assert identifier_digest("SH00001") in {m.identifier_digest for m in stored}
        assert {m.answered_by_user_id for m in stored} == {"user1"}

    def test_import_zip(self, service: ICalSyncService):
        csv_content = (
            "First Name,Last Name,Email,Birth Date,Phone Number,"
            "Street Address,City,State,ZIP Code,Active,Diagnosis,"
            "Assigned Practitioner,Payer Name,Member ID\n"
            "Alice,Apple,,,,,,,,Y,,Kurt Niemi,,\n"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("export/clients.csv", csv_content)
        result = service.import_clients("user1", "sessions_health", buf.getvalue(), "export.zip")
        assert result.imported == 1

    def test_import_bad_file(self, service: ICalSyncService):
        result = service.import_clients("user1", "sessions_health", b"not a csv", "data.txt")
        assert len(result.errors) == 1


class TestFeedUrlReviewFollowUps:
    def test_a_query_string_is_kept_and_read(self, service: ICalSyncService) -> None:
        url = "https://app.sessionshealth.com/calendars/abc.ics?token=t1"

        assert service._validate_feed_url("sessions_health", url) == url

    def test_a_refused_url_is_its_own_error_type(self, service: ICalSyncService) -> None:
        with pytest.raises(FeedUrlRefusedError):
            service._validate_feed_url("simplepractice", "https://evil.test/ical/x.ics")

    @pytest.mark.usefixtures("_encryption_key")
    @patch.object(ICalSyncService, "_fetch_feed", return_value="BEGIN:VCALENDAR\nnot a feed")
    def test_a_feed_that_cant_be_read_is_not_blamed_on_its_address(self, fetch: MagicMock) -> None:
        repo = InMemoryICalSyncConfigRepo()
        repo.save(
            ICalSyncConfig(
                user_id="user1",
                ehr_system="simplepractice",
                encrypted_feed_url=encrypt_tokens(
                    {"feed_url": "https://secure.simplepractice.com/ical/feed.ics"}
                ),
                connected_at=_now(),
            )
        )
        feed = ICalSyncService(
            config_repo=repo,  # type: ignore[arg-type]
            appointment_repo=InMemoryAppointmentRepository(),
            patient_repo=InMemoryPatientRepository(),
            mapping_repo=InMemoryPatientSourceMappingRepository(),
            external_events=InMemoryExternalCalendarEventRepository(),
        )

        [result] = feed.sync("user1")

        assert result.errors == ["Sync failed — could not fetch or parse feed"]
        stored = repo.get("user1", "simplepractice")
        assert stored is not None
        assert stored.last_sync_error == "Sync failed — could not fetch or parse feed"
