# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The calendar reader against the captured feed fixture.

Every expectation here is a fact about ``tests/fixtures/simplepractice_calendar``
as captured (plus the one authored edge file its README names), read through
the same reader the importer uses. When the feed changes shape the fixture is
re-captured and these numbers move with it; they are never edited to make a
parser pass.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from backend.app.migration.readers import detect_layout
from backend.app.migration.readers.calendar_folder import calendar_files
from backend.app.migration.readers.simplepractice_calendar import (
    SimplePracticeCalendar,
    read_simplepractice_calendar,
)

FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_calendar"
AVERY, BLAKE, CASEY, DANA = "112230097", "112230081", "112230132", "112230150"
#: The appointment three captured files carry, each with its own copy.
OVERLAPPING = "3793472377"


@pytest.fixture(scope="module")
def calendar() -> SimplePracticeCalendar:
    return read_simplepractice_calendar(FIXTURE)


def test_every_file_is_read_in_name_order(calendar: SimplePracticeCalendar) -> None:
    assert calendar.files == (
        "calendar/appointments-01.json",
        "calendar/appointments-02.json",
        "calendar/appointments-05.json",
        "calendar/appointments-06.json",
        "calendar/appointments-07.json",
        "calendar/appointments-edge-group-external.json",
    )
    assert calendar.unreadable == ()


def test_each_appointment_once_with_the_latest_copy_winning(
    calendar: SimplePracticeCalendar,
) -> None:
    ids = [a.source_id for a in calendar.appointments]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids)) == 6
    # Files 01 and 05 say recurring with no note; 06, captured last, says
    # neither recurring nor note-less. The last word is the feed's.
    latest = next(a for a in calendar.appointments if a.source_id == OVERLAPPING)
    assert latest.path == "calendar/appointments-06.json"
    assert latest.is_recurring is False
    assert latest.has_note is True
    assert latest.client_id == AVERY


def test_appointment_clients_are_joined_through_included(
    calendar: SimplePracticeCalendar,
) -> None:
    by_id = {a.source_id: a.client_id for a in calendar.appointments}
    assert by_id == {
        "3793470566": BLAKE,
        OVERLAPPING: AVERY,
        "3793478974": CASEY,
        "3793480001": DANA,
        "3793480002": BLAKE,
        "3793480003": CASEY,
    }


def test_strings_become_bools(calendar: SimplePracticeCalendar) -> None:
    for appointment in calendar.appointments:
        assert isinstance(appointment.full_day, bool)
        assert isinstance(appointment.is_recurring, bool)
        assert isinstance(appointment.has_note, bool)
    for client in calendar.clients:
        assert isinstance(client.is_minor, bool)
        assert isinstance(client.protected, bool)


def test_times_keep_the_feeds_own_offsets_across_the_daylight_change(
    calendar: SimplePracticeCalendar,
) -> None:
    october = next(a for a in calendar.appointments if a.source_id == OVERLAPPING)
    november = next(a for a in calendar.appointments if a.source_id == "3793480001")
    assert october.start_at.isoformat() == "2026-10-20T10:00:00-04:00"
    assert october.start_at.utcoffset() == timedelta(hours=-4)
    assert november.start_at.isoformat() == "2026-11-02T10:00:00-05:00"
    assert november.start_at.utcoffset() == timedelta(hours=-5)
    for appointment in calendar.appointments:
        assert appointment.start_at.tzinfo is not None
        assert appointment.end_at - appointment.start_at == timedelta(minutes=50)
        assert appointment.duration_minutes == 50


def test_attendance_is_read_as_the_feed_wrote_it(calendar: SimplePracticeCalendar) -> None:
    status = {a.source_id: a.attendance_status for a in calendar.appointments}
    assert status["3793480002"] == "No Show"
    assert status["3793480003"] == "Cancelled"
    assert status[OVERLAPPING] == "Show"
    assert [a.attended for a in calendar.appointments].count(True) == 4


def test_external_events_are_skipped_and_group_appointments_unsupported(
    calendar: SimplePracticeCalendar,
) -> None:
    assert calendar.skipped == {"external": 1}
    assert [(u.kind, u.source_id) for u in calendar.unsupported] == [("group", "9001")]


def test_client_names_come_from_the_client_entry_or_the_title(
    calendar: SimplePracticeCalendar,
) -> None:
    clients = {c.source_id: c for c in calendar.clients}
    assert sorted(clients) == [BLAKE, AVERY, CASEY, DANA]
    # The capture includes clients with ``isMinor`` alone and names the
    # client in the appointment's title.
    avery = clients[AVERY]
    assert (avery.first_name, avery.last_name) == ("Avery", "Client")
    assert avery.name_from == "title"
    assert avery.preferred_name is None
    assert avery.protected is False
    # The fuller entry carries the name itself.
    dana = clients[DANA]
    assert (dana.first_name, dana.last_name) == ("Dana", "Client")
    assert dana.name_from == "client"
    assert dana.preferred_name == "Dee"
    assert dana.preferred_full_name == "Dee Client"
    assert dana.hashed_id == "f3a9c1d2e4b5"
    assert dana.protected is False


def test_digest_follows_what_lands_not_the_permissions(tmp_path: Path) -> None:
    """A permission string moving between captures is not a changed appointment;
    a moved start time is."""
    source = json.loads((FIXTURE / "calendar" / "appointments-01.json").read_text())
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(source))
    base = read_simplepractice_calendar(tmp_path).appointments[0].digest

    source["data"][0]["attributes"]["permissions"] = "{}"
    source["data"][0]["attributes"]["hasNote"] = "true"
    (folder / "a.json").write_text(json.dumps(source))
    assert read_simplepractice_calendar(tmp_path).appointments[0].digest == base

    source["data"][0]["attributes"]["startTime"] = "2026-10-20T10:30:00-04:00"
    (folder / "a.json").write_text(json.dumps(source))
    assert read_simplepractice_calendar(tmp_path).appointments[0].digest != base


def test_a_file_that_is_not_a_response_is_reported_and_the_rest_read(tmp_path: Path) -> None:
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_bytes((FIXTURE / "calendar" / "appointments-07.json").read_bytes())
    (folder / "b.json").write_text('{"not": "a response"}')
    (folder / "c.json").write_text("{broken")
    calendar = read_simplepractice_calendar(tmp_path)
    assert len(calendar.appointments) == 2
    assert [rel for rel, _ in calendar.unreadable] == ["calendar/b.json", "calendar/c.json"]


def test_one_malformed_appointment_is_skipped_and_the_rest_of_its_file_read(
    tmp_path: Path,
) -> None:
    source = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    assert len(source["data"]) == 2
    source["data"][0]["attributes"]["endTime"] = None
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(source))
    calendar = read_simplepractice_calendar(tmp_path)
    assert [a.source_id for a in calendar.appointments] == [source["data"][1]["id"]]
    assert calendar.skipped == {"malformed": 1}
    assert calendar.unreadable == ()

    # A field of the wrong shape is the same: counted, not a failed read.
    source["data"][0]["attributes"] = ["not", "an", "object"]
    (folder / "a.json").write_text(json.dumps(source))
    assert read_simplepractice_calendar(tmp_path).skipped == {"malformed": 1}


def test_a_title_never_replaces_a_name_the_client_entry_gave(tmp_path: Path) -> None:
    full = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    client = next(e for e in full["included"] if e["id"] == BLAKE)
    client["attributes"].update({"firstName": "Blake", "lastName": "Client"})
    lean = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    for entry in lean["data"]:
        entry["attributes"]["title"] = "B. C."
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(full))
    (folder / "b.json").write_text(json.dumps(lean))  # later, but names from a title
    calendar = read_simplepractice_calendar(tmp_path)
    blake = next(c for c in calendar.clients if c.source_id == BLAKE)
    assert (blake.first_name, blake.last_name, blake.name_from) == ("Blake", "Client", "client")


def test_an_appointment_whose_client_is_not_included_is_skipped(tmp_path: Path) -> None:
    source = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    source["included"] = [e for e in source["included"] if e["id"] != CASEY]
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(source))
    calendar = read_simplepractice_calendar(tmp_path)
    assert [a.client_id for a in calendar.appointments] == [BLAKE]
    assert calendar.skipped == {"client_missing": 1}


def test_files_are_read_in_the_order_a_browser_saved_them(tmp_path: Path) -> None:
    folder = tmp_path / "calendar"
    folder.mkdir()
    names = ["appointments (10).json", "appointments.json", "appointments (2).json"]
    names += ["feed-10.json", "feed-2.json", "notes.txt"]
    for name in names:
        (folder / name).write_text("{}")
    assert [p.name for p in calendar_files(tmp_path)] == [
        "appointments.json",
        "appointments (2).json",
        "appointments (10).json",
        "feed-2.json",
        "feed-10.json",
    ]


def test_a_capitalised_calendar_folder_is_read(tmp_path: Path) -> None:
    folder = tmp_path / "Calendar"
    folder.mkdir()
    (folder / "a.json").write_bytes((FIXTURE / "calendar" / "appointments-07.json").read_bytes())
    assert detect_layout(tmp_path) == "calendar"
    assert len(read_simplepractice_calendar(tmp_path).appointments) == 2


def test_either_restriction_marks_a_client_protected(tmp_path: Path) -> None:
    source = json.loads(
        (FIXTURE / "calendar" / "appointments-edge-group-external.json").read_text()
    )
    dana = next(e for e in source["included"] if e["id"] == DANA)
    folder = tmp_path / "calendar"
    folder.mkdir()
    for flags, expected in (
        ({"enhancedDataProtection": "true", "revokedAdminIds": "[]"}, True),
        ({"enhancedDataProtection": "false", "revokedAdminIds": "[2156935]"}, True),
        ({"enhancedDataProtection": "false", "revokedAdminIds": "[]"}, False),
    ):
        dana["attributes"].update(flags)
        (folder / "a.json").write_text(json.dumps(source))
        clients = {c.source_id: c for c in read_simplepractice_calendar(tmp_path).clients}
        assert clients[DANA].protected is expected


def test_a_client_named_by_no_whole_name_is_skipped(tmp_path: Path) -> None:
    source = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    source["data"][0]["attributes"]["title"] = "Blake"
    source["data"][1]["attributes"]["title"] = ""
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(source))
    calendar = read_simplepractice_calendar(tmp_path)
    assert calendar.appointments == ()
    assert calendar.clients == ()
    assert calendar.skipped == {"no_name": 2}


def test_a_newer_copy_that_cannot_land_drops_the_older_one(tmp_path: Path) -> None:
    """The latest file wins even when it says the appointment can no longer
    land: the older copy is stale and is not landed in its place."""
    older = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    newer = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    newer["data"][0]["relationships"]["appointmentClients"]["data"] = []
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(older))
    (folder / "b.json").write_text(json.dumps(newer))
    calendar = read_simplepractice_calendar(tmp_path)
    assert [a.source_id for a in calendar.appointments] == [newer["data"][1]["id"]]
    assert calendar.skipped == {"no_client": 1}


def test_an_all_day_event_is_left_out(tmp_path: Path) -> None:
    source = json.loads((FIXTURE / "calendar" / "appointments-07.json").read_text())
    source["data"][0]["attributes"]["fullDay"] = "true"
    folder = tmp_path / "calendar"
    folder.mkdir()
    (folder / "a.json").write_text(json.dumps(source))
    calendar = read_simplepractice_calendar(tmp_path)
    assert [a.source_id for a in calendar.appointments] == [source["data"][1]["id"]]
    assert calendar.skipped == {"full_day": 1}
