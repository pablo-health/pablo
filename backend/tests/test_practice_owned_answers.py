# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Remembered answers belong to the practice, and one outside event books once.

What this proves over in-memory storage:

* an identifier is kept as a keyed digest — its kind readable, the identifier
  not, and a different key a different digest;
* a feed code's answer is the practice's, so one clinician's answer books a
  colleague's sessions; a calendar's answer is that calendar's;
* a clinician's answers from before answers were the practice's are adopted
  on the first read, the newer of two answers standing, once;
* two followers of one calendar book one appointment: the second links to
  the first's, and a request that loses the race links the same way.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from app.calendar_providers.source_identity import (
    GOOGLE_CALENDAR_SOURCE,
    answer_scope,
    answered_title_digest,
    calendar_source_identifier,
    ical_source,
)
from app.models.patient import Patient
from app.patients.identifiers import (
    PRACTICE_SCOPE,
    calendar_scope,
    identifier_digest,
    is_calendar_scope,
)
from app.patients.matching import MatchContext, PatientHint, match_patient, remember_match
from app.repositories.external_calendar_event import (
    ANSWER_CLIENT,
    ExternalCalendarEvent,
    InMemoryExternalCalendarEventRepository,
)
from app.repositories.ical_sync_config import ICalSyncConfig
from app.repositories.patient import InMemoryPatientRepository
from app.repositories.patient_source_mapping import (
    ANSWER_NOT_A_CLIENT,
    InMemoryPatientSourceMappingRepository,
    LegacyAnswer,
)
from app.scheduling_engine.exceptions import OutsideEventAlreadyBookedError
from app.scheduling_engine.models.appointment import Appointment, AppointmentStatus
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services.ical_sync_service import ICalSyncService
from app.services.outside_sessions import CALENDAR_NOT_KNOWN, OutsideSessions
from app.services.token_encryption import encrypt_tokens
from app.settings import get_settings
from app.utcnow import utc_now

if TYPE_CHECKING:
    from collections.abc import Generator

A = "clinician-a"
B = "clinician-b"
MAIN = "shared@group.calendar.google.test"
CALENDAR = calendar_scope(MAIN)
SH = "sessions_health"
SH_FEED = ical_source(SH)
SH_ICAL = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:sh-1
DTSTART:20990105T150000Z
DTEND:20990105T155000Z
SUMMARY:SH00001
END:VEVENT
END:VCALENDAR"""


def _new_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def _calendar_key(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """The secret identifiers are digested under; every remembered answer needs it."""
    monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", _new_key())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _patient(patient_id: str, first: str, last: str) -> Patient:
    now = utc_now()
    return Patient(id=patient_id, first_name=first, last_name=last, created_at=now, updated_at=now)


# --- The digest ---------------------------------------------------------------------


class TestIdentifierDigest:
    def test_keeps_the_kind_and_hides_the_identifier(self) -> None:
        assert identifier_digest("series:abc-123").startswith("series:")
        assert identifier_digest("shape:0123abcd").startswith("shape:")
        assert identifier_digest("SH00001").startswith("feed:")
        assert identifier_digest("Jane Adams").startswith("feed:")
        for plain, digest in (
            ("abc-123", identifier_digest("series:abc-123")),
            ("jane", identifier_digest("Jane Adams").lower()),
            ("sh00001", identifier_digest("SH00001").lower()),
        ):
            assert plain not in digest

    def test_compares_like_the_matcher_does(self) -> None:
        assert identifier_digest("J.A.") == identifier_digest("  j.a.  ")
        assert identifier_digest("series:X") == identifier_digest("series:x")
        assert identifier_digest("SH00001") != identifier_digest("SH00002")

    def test_changes_with_the_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        under_one_key = identifier_digest("SH00001")
        monkeypatch.setenv("GOOGLE_CALENDAR_ENCRYPTION_KEY", _new_key())
        get_settings.cache_clear()

        assert identifier_digest("SH00001") != under_one_key


class TestScopes:
    def test_a_feeds_answer_is_the_practices(self) -> None:
        assert answer_scope("simplepractice", None) == PRACTICE_SCOPE
        assert answer_scope("sessions_health", MAIN) == PRACTICE_SCOPE

    def test_a_calendars_answer_is_that_calendars(self) -> None:
        assert answer_scope(GOOGLE_CALENDAR_SOURCE, MAIN) == CALENDAR
        assert is_calendar_scope(CALENDAR)
        assert not is_calendar_scope(PRACTICE_SCOPE)

    def test_a_calendar_that_is_not_known_has_no_scope(self) -> None:
        assert answer_scope(GOOGLE_CALENDAR_SOURCE, None) is None


# --- Adoption -----------------------------------------------------------------------


def _legacy(
    user_id: str,
    source: str,
    identifier: str,
    patient_id: str | None,
    *,
    days_ago: int,
    answer: str = ANSWER_CLIENT,
) -> LegacyAnswer:
    return LegacyAnswer(
        user_id=user_id,
        source=source,
        source_identifier=identifier,
        patient_id=patient_id,
        answer=answer,
        created_at=datetime.now(UTC) - timedelta(days=days_ago),
    )


class TestAdoption:
    def test_a_feed_answer_is_adopted_into_the_practice_on_first_read(self) -> None:
        patients = InMemoryPatientRepository()
        patients.create(_patient("p1", "Pablo", "Bear"), A)
        mappings = InMemoryPatientSourceMappingRepository()
        mappings.remember_legacy(_legacy(A, SH, "SH00001", "p1", days_ago=3))

        hint = PatientHint(source=SH, source_identifier="SH00001", scope=PRACTICE_SCOPE)
        result = match_patient(hint, MatchContext.for_practice(A, patients, mappings))

        assert (result.patient_id, result.evidence) == ("p1", "remembered")
        assert mappings.legacy_answers() == []
        [stored] = mappings.list_by_source(PRACTICE_SCOPE, SH)
        assert (stored.identifier_digest, stored.answered_by_user_id) == (
            identifier_digest("SH00001"),
            A,
        )
        assert stored.session_clinician_user_id is None

    def test_a_calendar_answer_is_adopted_into_the_main_calendar_only(self) -> None:
        patients = InMemoryPatientRepository()
        patients.create(_patient("p1", "Jane", "Smith"), A)
        mappings = InMemoryPatientSourceMappingRepository()
        identifier = calendar_source_identifier("wk", "", 0, "00:00")
        mappings.remember_legacy(_legacy(A, GOOGLE_CALENDAR_SOURCE, identifier, "p1", days_ago=3))
        hint = PatientHint(
            source=GOOGLE_CALENDAR_SOURCE,
            source_identifier=identifier,
            scope=calendar_scope("team@group.calendar.google.test"),
        )

        # Read under another calendar, with the main one not known: left alone.
        unknown = MatchContext.for_practice(A, patients, mappings)
        assert match_patient(hint, unknown).evidence != "remembered"
        assert len(mappings.legacy_answers()) == 1

        # Read under another calendar, with the main one known: still left alone.
        other = MatchContext.for_practice(A, patients, mappings, main_calendar_id=MAIN)
        assert match_patient(hint, other).evidence != "remembered"
        assert len(mappings.legacy_answers()) == 1

        # Read under the main calendar: adopted there, as the answerer's session.
        result = match_patient(hint.model_copy(update={"scope": CALENDAR}), other)
        assert (result.patient_id, result.evidence) == ("p1", "remembered")
        assert mappings.legacy_answers() == []
        [stored] = mappings.list_by_source(CALENDAR, GOOGLE_CALENDAR_SOURCE)
        assert (stored.answered_by_user_id, stored.session_clinician_user_id) == (A, A)

    def test_the_newer_answer_stands_whichever_side_it_is_on(self) -> None:
        patients = InMemoryPatientRepository()
        patients.create(_patient("old", "Pablo", "Bear"), A)
        patients.create(_patient("new", "Lulu", "Niemi"), A)
        mappings = InMemoryPatientSourceMappingRepository()
        remember_match(
            SH,
            "SH00001",
            "new",
            MatchContext.for_practice(B, patients, mappings),
            scope=PRACTICE_SCOPE,
        )
        mappings.remember_legacy(_legacy(A, SH, "SH00001", "old", days_ago=30))
        mappings.remember_legacy(_legacy(A, SH, "SH00002", "old", days_ago=30))
        remember_match(
            SH,
            "SH00002",
            "old",
            MatchContext.for_practice(B, patients, mappings),
            scope=PRACTICE_SCOPE,
        )
        [held] = [m for m in mappings.list_by_source(PRACTICE_SCOPE, SH) if m.patient_id == "old"]
        held.created_at = datetime.now(UTC) - timedelta(days=60)
        mappings.remember_legacy(
            _legacy(A, SH, "SH00002", None, days_ago=10, answer=ANSWER_NOT_A_CLIENT)
        )

        ctx = MatchContext.for_practice(A, patients, mappings)
        first = match_patient(
            PatientHint(source=SH, source_identifier="SH00001", scope=PRACTICE_SCOPE), ctx
        )
        second = match_patient(
            PatientHint(source=SH, source_identifier="SH00002", scope=PRACTICE_SCOPE), ctx
        )

        # The practice's newer answer outlives the old row for SH00001; the
        # old row's newer "not a client" replaces the practice's for SH00002.
        assert (first.patient_id, first.evidence) == ("new", "remembered")
        assert second.evidence == "not_a_client"
        assert mappings.legacy_answers() == []

    def test_a_second_read_changes_nothing(self) -> None:
        patients = InMemoryPatientRepository()
        patients.create(_patient("p1", "Pablo", "Bear"), A)
        mappings = InMemoryPatientSourceMappingRepository()
        mappings.remember_legacy(_legacy(A, SH, "SH00001", "p1", days_ago=3))
        hint = PatientHint(source=SH, source_identifier="SH00001", scope=PRACTICE_SCOPE)

        match_patient(hint, MatchContext.for_practice(A, patients, mappings))
        before = [(m.doc_id, m.created_at) for m in mappings.list_by_source(PRACTICE_SCOPE, SH)]
        match_patient(hint, MatchContext.for_practice(A, patients, mappings))

        assert [(m.doc_id, m.created_at) for m in mappings.list_by_source(PRACTICE_SCOPE, SH)] == (
            before
        )


# --- A feed code answered once books for everyone -------------------------------------


class _Practice:
    """Two clinicians over one practice's storage, each following the same feed."""

    def __init__(self) -> None:
        self.patients = InMemoryPatientRepository()
        self.appointments = InMemoryAppointmentRepository()
        self.mappings = InMemoryPatientSourceMappingRepository()
        self.events = InMemoryExternalCalendarEventRepository()
        self.outside = OutsideSessions(
            self.events, self.appointments, self.patients, self.mappings, main_calendar_id=MAIN
        )

    def shared_client(self, patient_id: str, first: str, last: str) -> None:
        self.patients.create(_patient(patient_id, first, last), A)
        self.patients.grant_access(patient_id, B)
        self.appointments.grant_access(patient_id, A)
        self.appointments.grant_access(patient_id, B)

    def feed(self, user_id: str) -> ICalSyncService:
        configs = MagicMock()
        configs.list_by_user.return_value = [
            ICalSyncConfig(
                user_id=user_id,
                ehr_system=SH,
                encrypted_feed_url=encrypt_tokens({"feed_url": "https://x.test/sh"}),
                connected_at=utc_now(),
            )
        ]
        return ICalSyncService(
            config_repo=configs,
            appointment_repo=self.appointments,
            patient_repo=self.patients,
            mapping_repo=self.mappings,
            external_events=self.events,
        )

    def sync(self, user_id: str) -> Any:
        with patch.object(ICalSyncService, "_fetch_feed", return_value=SH_ICAL):
            [result] = self.feed(user_id).sync(user_id, SH)
        return result


class TestAFeedCodeIsThePractices:
    def test_answered_by_one_clinician_it_books_for_another(self) -> None:
        practice = _Practice()
        practice.shared_client("p1", "Pablo", "Bear")
        assert practice.sync(A).created == 0
        [held] = practice.events.list_open(A)

        practice.outside.answer(A, SH_FEED, "SH00001", patient_id="p1")
        result = practice.sync(B)

        assert result.created == 1
        [booked] = practice.appointments.list_by_ical_source(B, SH)
        assert (booked.patient_id, booked.user_id) == ("p1", B)
        assert practice.events.list_open(B) == []
        # A's own session was booked by the answer, under A's name.
        [mine] = practice.appointments.list_by_ical_source(A, SH)
        assert (mine.patient_id, mine.user_id, mine.id != booked.id) == ("p1", A, True)
        assert held.source_event_id == "sh-1"

    def test_the_answer_records_who_gave_it(self) -> None:
        practice = _Practice()
        practice.shared_client("p1", "Pablo", "Bear")

        practice.outside.answer(A, SH_FEED, "SH00001", patient_id="p1")

        [stored] = practice.mappings.list_by_source(PRACTICE_SCOPE, SH)
        assert (stored.answered_by_user_id, stored.scope, stored.session_clinician_user_id) == (
            A,
            PRACTICE_SCOPE,
            None,
        )


# --- Two followers of one calendar ---------------------------------------------------


def _shared_event(event_id: str, days: int) -> dict[str, Any]:
    start = (utc_now() + timedelta(days=days)).replace(minute=0, second=0, microsecond=0)
    return {
        "google_event_id": event_id,
        "status": "confirmed",
        "summary": "Jane Smith",
        "series_id": "wk",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": (start + timedelta(minutes=50)).isoformat()},
    }


class TestTwoFollowersOfOneCalendar:
    def test_one_answer_books_once_and_the_second_follower_links_to_it(self) -> None:
        practice = _Practice()
        practice.shared_client("p1", "Jane", "Smith")
        for user_id in (A, B):
            practice.outside.ingest_google(user_id, [_shared_event("e1", 3)], calendar_id=MAIN)
        series = calendar_source_identifier("wk", "", 0, "00:00")

        [a_row] = practice.outside.answer(A, GOOGLE_CALENDAR_SOURCE, series, patient_id="p1")
        [b_row] = practice.outside.answer(B, GOOGLE_CALENDAR_SOURCE, series, patient_id="p1")

        assert a_row.appointment_id is not None
        assert b_row.appointment_id == a_row.appointment_id
        assert b_row.answer == ANSWER_CLIENT
        live = [
            a
            for a in practice.appointments._appointments.values()
            if a.outside_event_id == "e1" and a.status != AppointmentStatus.CANCELLED
        ]
        assert [a.id for a in live] == [a_row.appointment_id]

    def test_the_first_answer_settles_it_for_the_other_follower_on_their_next_read(self) -> None:
        practice = _Practice()
        practice.shared_client("p1", "Jane", "Smith")
        practice.outside.ingest_google(A, [_shared_event("e1", 3)], calendar_id=MAIN)
        series = calendar_source_identifier("wk", "", 0, "00:00")
        [a_row] = practice.outside.answer(A, GOOGLE_CALENDAR_SOURCE, series, patient_id="p1")

        ingested = practice.outside.ingest_google(B, [_shared_event("e1", 3)], calendar_id=MAIN)

        assert ingested.booked == []
        assert practice.events.list_open(B) == []
        b_row = practice.events.get(B, GOOGLE_CALENDAR_SOURCE, "e1")
        assert b_row is not None
        assert (b_row.answer, b_row.appointment_id) == (ANSWER_CLIENT, a_row.appointment_id)

    def test_a_request_that_loses_the_race_links_instead_of_failing(self) -> None:
        """The pre-check misses, the store refuses the duplicate, the row links."""

        class _Racing(InMemoryAppointmentRepository):
            def __init__(self) -> None:
                super().__init__()
                self.first_lookup = True

            def outside_appointment_id(
                self, source: str, calendar_id: str | None, event_id: str, user_id: str
            ) -> str | None:
                if self.first_lookup:
                    self.first_lookup = False
                    return None
                return super().outside_appointment_id(source, calendar_id, event_id, user_id)

        practice = _Practice()
        practice.appointments = _Racing()
        practice.outside = OutsideSessions(
            practice.events,
            practice.appointments,
            practice.patients,
            practice.mappings,
            main_calendar_id=MAIN,
        )
        practice.shared_client("p1", "Jane", "Smith")
        start = utc_now() + timedelta(days=3)
        theirs = practice.appointments.create(
            Appointment(
                id="theirs",
                user_id=A,
                patient_id="p1",
                title="Session",
                start_at=start,
                end_at=start + timedelta(minutes=50),
                duration_minutes=50,
                status=AppointmentStatus.CONFIRMED,
                session_type="individual",
                outside_source=GOOGLE_CALENDAR_SOURCE,
                outside_event_id="e1",
                outside_calendar_id=MAIN,
                created_at=utc_now(),
            )
        )
        practice.appointments.first_lookup = True
        practice.outside.ingest_google(B, [_shared_event("e1", 3)], calendar_id=MAIN)

        [b_row] = practice.outside.answer(
            B,
            GOOGLE_CALENDAR_SOURCE,
            calendar_source_identifier("wk", "", 0, "00:00"),
            patient_id="p1",
        )

        assert b_row.appointment_id == theirs.id

    def test_the_store_refuses_a_second_live_appointment_for_one_event(self) -> None:
        appointments = InMemoryAppointmentRepository()
        start = utc_now() + timedelta(days=3)

        def booking(appointment_id: str, user_id: str) -> Appointment:
            return Appointment(
                id=appointment_id,
                user_id=user_id,
                patient_id="p1",
                title="Session",
                start_at=start,
                end_at=start + timedelta(minutes=50),
                duration_minutes=50,
                status=AppointmentStatus.CONFIRMED,
                session_type="individual",
                outside_source=GOOGLE_CALENDAR_SOURCE,
                outside_event_id="e1",
                outside_calendar_id=MAIN,
                created_at=utc_now(),
            )

        appointments.create(booking("first", A))
        with pytest.raises(OutsideEventAlreadyBookedError):
            appointments.create(booking("second", B))


class TestAnsweringNeedsTheCalendar:
    def test_a_row_from_before_calendars_were_recorded_needs_the_main_one(self) -> None:
        practice = _Practice()
        practice.shared_client("p1", "Jane", "Smith")
        unknown = OutsideSessions(
            practice.events, practice.appointments, practice.patients, practice.mappings
        )
        start = (utc_now() + timedelta(days=3)).replace(minute=0, second=0, microsecond=0)
        practice.events.save(
            ExternalCalendarEvent(
                id="row-e1",
                user_id=A,
                source=GOOGLE_CALENDAR_SOURCE,
                source_event_id="e1",
                source_series_id="wk",
                start_at=start,
                end_at=start + timedelta(minutes=50),
                title="Jane Smith",
            )
        )
        series = calendar_source_identifier("wk", "", 0, "00:00")

        with pytest.raises(ValueError, match=CALENDAR_NOT_KNOWN):
            unknown.answer(A, GOOGLE_CALENDAR_SOURCE, series, patient_id="p1")
        assert practice.mappings.list_by_source(CALENDAR, GOOGLE_CALENDAR_SOURCE) == []

        # Told which calendar is the main one, the same row answers under it.
        [row] = unknown.with_main_calendar(MAIN).answer(
            A, GOOGLE_CALENDAR_SOURCE, series, patient_id="p1"
        )
        assert row.appointment_id is not None
        [stored] = practice.mappings.list_by_source(CALENDAR, GOOGLE_CALENDAR_SOURCE)
        assert stored.answered_title == answered_title_digest("Jane Smith")
