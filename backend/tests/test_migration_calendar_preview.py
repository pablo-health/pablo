# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The calendar preview over the captured feed fixture, and how an upload is
told apart as an archive, a calendar folder, or neither."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.migration.ledger import LedgerEntry, extract_zip
from backend.app.migration.preview import (
    Clock,
    ExistingPatient,
    PreviewInputs,
    build_calendar_preview,
    decisions_complete,
)
from backend.app.migration.readers import detect_layout
from backend.app.migration.readers.simplepractice_calendar import (
    SimplePracticeCalendar,
    read_simplepractice_calendar,
)

FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_calendar"
ARCHIVE_FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_export"
AVERY, BLAKE, CASEY, DANA = "112230097", "112230081", "112230132", "112230150"
ET = ZoneInfo("America/New_York")
#: The preview is judged on the day before the fixture's 2026-10-20 visit.
CLOCK = Clock(datetime(2026, 10, 19, 16, 0, tzinfo=UTC), ET)


@pytest.fixture(scope="module")
def calendar() -> SimplePracticeCalendar:
    return read_simplepractice_calendar(FIXTURE)


def _preview(
    calendar: SimplePracticeCalendar,
    *,
    ledger: dict[tuple[str, str], LedgerEntry] | None = None,
    existing_patients: list[ExistingPatient] | None = None,
    clock: Clock = CLOCK,
) -> dict:
    inputs = PreviewInputs(ledger=ledger or {}, existing_patients=existing_patients or [])
    return build_calendar_preview(calendar, inputs, clock=clock)


def test_first_run_is_all_new(calendar: SimplePracticeCalendar) -> None:
    p = _preview(calendar)
    assert p["source_system"] == "simplepractice"
    assert p["layout"] == "calendar"
    assert [c["card_id"] for c in p["clients"]] == [BLAKE, AVERY, CASEY, DANA]
    assert p["counts"]["contact"] == {"new": 4}
    # Two past visits marked attended, today's-or-later two, two past misses.
    assert p["counts"]["appointment"] == {"new": 4, "skipped": 2}
    assert p["providers"] == []
    assert p["non_client_contacts"] == []
    assert p["questions"] == {"same_name": [], "duplicates": [], "providers": []}
    assert decisions_complete(p, {}) == []


def test_each_appointment_says_how_it_lands(calendar: SimplePracticeCalendar) -> None:
    by_id = {r["source_id"]: r for r in _preview(calendar)["records"]}
    assert {k: r["lands_as"] for k, r in by_id.items()} == {
        "3793470566": "completed",  # 2026-10-13, Show
        "3793472377": "scheduled",  # 2026-10-20
        "3793478974": "completed",  # 2026-10-14, Show
        "3793480001": "scheduled",  # 2026-11-02
        "3793480002": None,  # 2026-10-06, No Show
        "3793480003": None,  # 2026-10-07, Cancelled
    }
    for record in by_id.values():
        assert record["record_type"] == "appointment"
        assert record["kind"] == "appointment"
        assert record["label"] == "Session"
        assert record["landable"] is (record["lands_as"] is not None)
        assert record["candidates"] == [record["card_id"]]
    assert by_id["3793472377"]["when"] == "2026-10-20"
    assert by_id["3793472377"]["start_at"] == "2026-10-20T10:00:00-04:00"
    assert by_id["3793480002"]["reason"]


def test_todays_appointment_is_still_scheduled_in_the_practice_zone(
    calendar: SimplePracticeCalendar,
) -> None:
    """At 23:30 Eastern on the 20th the 10:00 visit is today's and stays
    scheduled, whatever the clock says; the same instant is already the 21st
    in UTC, where the visit was an earlier day's attended one."""
    late_that_night = Clock(datetime(2026, 10, 21, 3, 30, tzinfo=UTC), ET)
    records = {r["source_id"]: r for r in _preview(calendar, clock=late_that_night)["records"]}
    assert records["3793472377"]["lands_as"] == "scheduled"
    in_utc = Clock(late_that_night.now, UTC)
    records = {r["source_id"]: r for r in _preview(calendar, clock=in_utc)["records"]}
    assert records["3793472377"]["lands_as"] == "completed"


def test_group_and_external_entries_are_reported_not_landed(
    calendar: SimplePracticeCalendar,
) -> None:
    what = {c["what"]: c["count"] for c in _preview(calendar)["cannot_land"]}
    assert what == {"group_appointment": 1, "external_event": 1, "not_attended": 2}


def test_preview_carries_no_feed_content(calendar: SimplePracticeCalendar) -> None:
    text = json.dumps(_preview(calendar))
    assert "permissions" not in text
    assert "Staff meeting" not in text  # the outside event's title
    assert "Skills group" not in text  # the group appointment's title
    assert "f3a9c1d2e4b5" not in text  # a client's hashed id
    # Names sit on the client cards, as they do for an archive; never on a record.
    for record in _preview(calendar)["records"]:
        assert "Client" not in json.dumps(record)


def test_protected_clients_are_flagged_for_the_screen(calendar: SimplePracticeCalendar) -> None:
    flags = {c["card_id"]: c["protected"] for c in _preview(calendar)["clients"]}
    assert flags == {BLAKE: False, AVERY: False, CASEY: False, DANA: True}
    dana = next(c for c in _preview(calendar)["clients"] if c["card_id"] == DANA)
    assert dana["display_name"] == "Dana Client"
    assert dana["preferred_name"] == "Dee"


def test_a_same_named_patient_is_a_question_never_a_merge(
    calendar: SimplePracticeCalendar,
) -> None:
    existing = [ExistingPatient("p-1", "avery", "CLIENT", None, None)]
    p = _preview(calendar, existing_patients=existing)
    avery = next(c for c in p["clients"] if c["card_id"] == AVERY)
    assert avery["existing_patient_id"] is None
    assert avery["match_evidence"] is None
    assert avery["possible_duplicates"] == ["p-1"]
    assert p["questions"]["duplicates"] == [{"card_id": AVERY, "possible_duplicates": ["p-1"]}]
    assert p["counts"]["contact"] == {"new": 4, "unresolved": 1}
    assert decisions_complete(p, {}) == [f"duplicate decision for client {AVERY}"]
    assert decisions_complete(p, {"duplicates": {AVERY: "create"}}) == []


def test_a_preferred_name_matching_a_patient_is_also_a_question(
    calendar: SimplePracticeCalendar,
) -> None:
    existing = [ExistingPatient("p-2", "Dee", "Client", None, None)]
    p = _preview(calendar, existing_patients=existing)
    dana = next(c for c in p["clients"] if c["card_id"] == DANA)
    assert dana["possible_duplicates"] == ["p-2"]


def test_second_run_reads_as_a_delta(calendar: SimplePracticeCalendar) -> None:
    landed = {
        ("appointment", a.source_id): LedgerEntry(
            "appointment",
            a.source_id,
            "appointments",
            f"a-{a.source_id}",
            a.digest,
            "landed",
            "r-1",
        )
        for a in calendar.appointments
    }
    landed.update(
        {
            ("contact", c.source_id): LedgerEntry(
                "contact", c.source_id, "patients", f"p-{c.source_id}", c.digest, "landed", "r-1"
            )
            for c in calendar.clients
        }
    )
    p = _preview(calendar, ledger=landed)
    assert p["counts"]["appointment"] == {"unchanged": 4, "skipped": 2}
    assert p["counts"]["contact"] == {"unchanged": 4}
    assert {c["match_evidence"] for c in p["clients"]} == {"ledger"}
    assert {c["existing_patient_id"] for c in p["clients"]} == {
        f"p-{c}" for c in (AVERY, BLAKE, CASEY, DANA)
    }

    # One appointment moved in the feed since: its digest no longer matches.
    moved = ("appointment", "3793472377")
    landed[moved] = LedgerEntry(
        "appointment", "3793472377", "appointments", "a-3793472377", "stale", "landed", "r-1"
    )
    p = _preview(calendar, ledger=landed)
    states = {r["source_id"]: r["state"] for r in p["records"]}
    assert states["3793472377"] == "changed"
    assert p["counts"]["appointment"] == {"unchanged": 3, "changed": 1, "skipped": 2}

    # Undone, an earlier landing counts for nothing.
    landed[moved] = LedgerEntry(
        "appointment", "3793472377", "appointments", "a-3793472377", "stale", "undone", "r-1"
    )
    states = {r["source_id"]: r["state"] for r in _preview(calendar, ledger=landed)["records"]}
    assert states["3793472377"] == "new"


# --------------------------------------------------------------------------- layout


def test_detect_layout_tells_the_two_apart(tmp_path: Path) -> None:
    assert detect_layout(FIXTURE) == "calendar"
    assert detect_layout(ARCHIVE_FIXTURE) == "archive"
    assert detect_layout(tmp_path) is None
    (tmp_path / "calendar").mkdir()
    assert detect_layout(tmp_path) is None  # an empty calendar folder holds nothing to read
    (tmp_path / "calendar" / "a.json").write_text("{}")
    assert detect_layout(tmp_path) == "calendar"
    (tmp_path / "Contacts").mkdir()
    assert detect_layout(tmp_path) == "mixed"


def _zipped(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_a_zip_of_the_calendar_folder_alone_keeps_its_folder(tmp_path: Path) -> None:
    """Zipping the saved folder gives one top-level ``calendar/`` directory;
    the unwrap that serves an export must not flatten it."""
    data = (FIXTURE / "calendar" / "appointments-07.json").read_bytes()
    extract_zip(_zipped({"calendar/appointments-07.json": data}), tmp_path / "bare")
    assert detect_layout(tmp_path / "bare") == "calendar"
    extract_zip(
        _zipped({"My saved feed/calendar/appointments-07.json": data}), tmp_path / "wrapped"
    )
    assert detect_layout(tmp_path / "wrapped") == "calendar"
