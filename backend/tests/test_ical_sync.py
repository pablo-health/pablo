# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for iCal calendar sync service."""

from __future__ import annotations

import base64
import io
import os
import zipfile
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
from app.models.patient import Patient
from app.repositories.ical_sync_config import ICalSyncConfig
from app.utcnow import utc_now_iso

if TYPE_CHECKING:
    from app.repositories.ical_client_mapping import ICalClientMapping
from app.repositories.patient import InMemoryPatientRepository
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.ical_sync_service import ICalSyncService
from app.services.token_encryption import encrypt_tokens

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


def _now() -> str:
    return utc_now_iso()


def _make_patient(patient_id: str, first: str, last: str, user_id: str = "user1") -> Patient:
    now = _now()
    return Patient(
        id=patient_id,
        user_id=user_id,
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
        self, user_id: str, ehr_system: str, *, error: str | None = None
    ) -> None:
        key = f"{user_id}_{ehr_system}"
        if key in self._configs:
            self._configs[key].last_synced_at = _now()
            self._configs[key].last_sync_error = error


class InMemoryICalClientMappingRepo:
    """In-memory client mapping repo for tests."""

    def __init__(self) -> None:
        self._mappings: dict[str, ICalClientMapping] = {}

    def get(
        self, user_id: str, ehr_system: str, client_identifier: str
    ) -> ICalClientMapping | None:
        key = f"{user_id}_{ehr_system}_{client_identifier}"
        return self._mappings.get(key)

    def list_by_user(self, user_id: str) -> list[ICalClientMapping]:
        return [m for m in self._mappings.values() if m.user_id == user_id]

    def list_by_source(self, user_id: str, ehr_system: str) -> list[ICalClientMapping]:
        return [
            m
            for m in self._mappings.values()
            if m.user_id == user_id and m.ehr_system == ehr_system
        ]

    def save(self, mapping: ICalClientMapping) -> None:
        self._mappings[mapping.doc_id] = mapping

    def delete(self, user_id: str, ehr_system: str, client_identifier: str) -> bool:
        key = f"{user_id}_{ehr_system}_{client_identifier}"
        if key in self._mappings:
            del self._mappings[key]
            return True
        return False


@pytest.fixture
def _encryption_key():
    """Set up a test encryption key."""
    key = base64.b64encode(os.urandom(32)).decode("ascii")
    with patch.dict(os.environ, {"GOOGLE_CALENDAR_ENCRYPTION_KEY": key}):
        yield


@pytest.fixture
def service():
    """Create an ICalSyncService with in-memory repos."""
    return ICalSyncService(
        config_repo=InMemoryICalSyncConfigRepo(),  # type: ignore[arg-type]
        appointment_repo=InMemoryAppointmentRepository(),
        patient_repo=InMemoryPatientRepository(),
        mapping_repo=InMemoryICalClientMappingRepo(),  # type: ignore[arg-type]
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
        assert "18:00:00" in e.start_at
        assert "19:00:00" in e.end_at


class TestClientMatching:
    """Tests for client identifier extraction and matching."""

    def test_extract_sp_initials(self, service: ICalSyncService):
        assert service._extract_client_identifier("simplepractice", "J.A. Appointment") == "J.A."

    def test_extract_sp_full_name(self, service: ICalSyncService):
        assert (
            service._extract_client_identifier("simplepractice", "Jane Adams Appointment")
            == "Jane Adams"
        )

    def test_extract_sh_code(self, service: ICalSyncService):
        assert service._extract_client_identifier("sessions_health", "SH00001") == "SH00001"

    def test_match_sp_unique_initials(self, service: ICalSyncService):
        patients = [
            _make_patient("p1", "Jane", "Adams"),
            _make_patient("p2", "Bob", "Smith"),
        ]
        result = service._match_sp_patient("J.A.", patients)
        assert result == "p1"

    def test_match_sp_ambiguous_initials(self, service: ICalSyncService):
        patients = [
            _make_patient("p1", "Jane", "Adams"),
            _make_patient("p2", "John", "Adams"),
        ]
        result = service._match_sp_patient("J.A.", patients)
        assert result == ""  # Ambiguous — can't determine

    def test_match_sp_full_name(self, service: ICalSyncService):
        patients = [
            _make_patient("p1", "Jane", "Adams"),
            _make_patient("p2", "Bob", "Smith"),
        ]
        result = service._match_sp_patient("Jane Adams", patients)
        assert result == "p1"

    def test_match_via_saved_mapping(self, service: ICalSyncService):
        mappings = {"SH00001": "patient-abc"}
        patients: list[Patient] = []
        result = service._match_patient("sessions_health", "SH00001", mappings, patients)
        assert result == "patient-abc"


class TestSyncDiff:
    """Tests for the sync create/update/delete logic."""

    @pytest.fixture
    def sync_service(self, _encryption_key: Any):
        """Service with pre-populated config."""
        config_repo = InMemoryICalSyncConfigRepo()
        appt_repo = InMemoryAppointmentRepository()
        patient_repo = InMemoryPatientRepository()
        mapping_repo = InMemoryICalClientMappingRepo()

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
            mapping_repo=mapping_repo,  # type: ignore[arg-type]
        )
        return svc

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_initial_sync_creates_appointments(
        self, mock_fetch: MagicMock, sync_service: ICalSyncService
    ):
        mock_fetch.return_value = SP_ICAL_DATA
        results = sync_service.sync("user1", "simplepractice")

        assert len(results) == 1
        result = results[0]
        assert result.created == 2
        assert result.updated == 0
        assert result.deleted == 0

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_second_sync_no_changes(self, mock_fetch: MagicMock, sync_service: ICalSyncService):
        mock_fetch.return_value = SP_ICAL_DATA
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
        mock_fetch.return_value = SP_ICAL_DATA
        sync_service.sync("user1", "simplepractice")

        # Second sync with one event removed
        reduced_data = SP_ICAL_DATA.replace(
            "BEGIN:VEVENT\nDTSTAMP:20260325T005244Z\n"
            "UID:3426439378\n"
            "DTSTART;TZID=America/New_York:20260323T200000\n"
            "DTEND;TZID=America/New_York:20260323T205000\n"
            "SUMMARY:P.B. Appointment\n"
            "URL;VALUE=URI:https://video.simplepractice.com/appt-485bc95d4f126fadb091e02f240ea244\n"
            "END:VEVENT\n",
            "",
        )
        mock_fetch.return_value = reduced_data
        results = sync_service.sync("user1", "simplepractice")
        result = results[0]
        assert result.deleted == 1
        assert result.unchanged == 1

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_ehr_appointment_url_set(self, mock_fetch: MagicMock, sync_service: ICalSyncService):
        mock_fetch.return_value = SP_ICAL_DATA
        sync_service.sync("user1", "simplepractice")

        appts = sync_service._appt_repo.list_by_ical_source("user1", "simplepractice")
        sp_appt = next(a for a in appts if a.ical_uid == "3415461692")
        assert (
            sp_appt.ehr_appointment_url
            == "https://secure.simplepractice.com/appointments/3415461692"
        )

    @patch.object(ICalSyncService, "_fetch_feed")
    def test_video_link_extracted(self, mock_fetch: MagicMock, sync_service: ICalSyncService):
        mock_fetch.return_value = SP_ICAL_DATA
        sync_service.sync("user1", "simplepractice")

        appts = sync_service._appt_repo.list_by_ical_source("user1", "simplepractice")
        video_appt = next(a for a in appts if a.ical_uid == "3426439378")
        assert video_appt.video_link is not None
        assert urlparse(video_appt.video_link).hostname == "video.simplepractice.com"


class TestUrlValidation:
    """Tests for feed URL validation."""

    def test_valid_sp_url(self, service: ICalSyncService):
        service._validate_feed_url(
            "simplepractice",
            "https://secure.simplepractice.com/ical/abc123",
        )

    def test_invalid_sp_url(self, service: ICalSyncService):
        with pytest.raises(ValueError, match="hostname must be"):
            service._validate_feed_url("simplepractice", "https://evil.com/feed")

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

    def test_invalid_sh_url(self, service: ICalSyncService):
        with pytest.raises(ValueError, match="hostname must be"):
            service._validate_feed_url("sessions_health", "https://evil.com/feed")

    def test_unsupported_ehr(self, service: ICalSyncService):
        with pytest.raises(ValueError, match="Unsupported"):
            service._validate_feed_url("unknown_ehr", "https://example.com")


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
        service._patient_repo.create(patient)

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

        # Verify SH00001 maps to Pablo Bear
        mapping = service._mapping_repo.get("user1", "sessions_health", "SH00001")  # type: ignore[union-attr]
        assert mapping is not None

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
