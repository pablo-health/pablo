# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The SimplePractice reader against the captured export fixture.

Every expectation here is a fact about ``tests/fixtures/simplepractice_export``
as captured, read through the same extractor the importer uses. When the
source system changes its layout the fixture is re-captured and these
numbers move with it; the numbers are never edited to make a parser pass.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.migration.readers.simplepractice import (
    SimplePracticeArchive,
    read_simplepractice_archive,
)

FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_export"
NO_DOB_FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_export_same_name_no_dob"
ET = ZoneInfo("America/New_York")


@pytest.fixture(scope="module")
def archive() -> SimplePracticeArchive:
    return read_simplepractice_archive(FIXTURE)


@pytest.fixture(scope="module")
def no_dob_archive() -> SimplePracticeArchive:
    return read_simplepractice_archive(NO_DOB_FIXTURE)


def test_everything_in_the_archive_is_recognised(archive: SimplePracticeArchive) -> None:
    assert archive.unrecognized == ()
    assert archive.unreadable == ()


def test_contact_cards(archive: SimplePracticeArchive) -> None:
    cards = {c.source_id: c for c in archive.contacts}
    assert sorted(cards) == ["112092152", "112092153", "112092194", "112093920"]

    lulu = cards["112092152"]
    assert lulu.display_name == "Lulu L. Llama"
    assert lulu.formatted_name == "Lulu Llama"
    assert lulu.folder_name == "Lulu Llama"
    assert lulu.birthday == date(1990, 3, 14)
    assert lulu.email == "lulu.llama@example.com"
    assert lulu.phone == "5555550100"
    assert lulu.address is not None
    assert (lulu.address.street, lulu.address.city, lulu.address.state) == (
        "42 Llama Lane",
        "Llamaville",
        "GA",
    )

    # The middle initial lives only in the file name: the card body never carries it.
    pablo_a = cards["112092194"]
    assert pablo_a.display_name == "Pablo A. Bear"
    assert pablo_a.formatted_name == "Pablo Bear"
    assert pablo_a.folder_name == "Pablo Bear"
    assert pablo_a.birthday == date(2025, 1, 1)

    pablo = cards["112093920"]
    assert pablo.display_name == "Pablo Bear"
    assert pablo.birthday == date(2025, 3, 1)

    lola = cards["112092153"]
    assert lola.birthday is None
    assert lola.address is None


def test_note_kinds_and_counts(archive: SimplePracticeArchive) -> None:
    kinds = sorted(n.kind for n in archive.notes)
    assert kinds == (
        ["administrative"]
        + ["chart"]
        + ["progress"] * 6
        + ["psychotherapy"] * 6
        + ["treatment_plan"]
    )


def test_progress_note_header_and_body(archive: SimplePracticeArchive) -> None:
    note = next(n for n in archive.notes if n.source_id == "1007836363")
    assert note.kind == "progress"
    assert note.title == "Progress Note"
    assert note.client_display_name == "Lulu L. Llama"
    assert note.client_dob == date(1990, 3, 14)
    assert note.provider_name == "Avery Provider"
    assert note.appointment is not None
    assert note.appointment.kind == "Individual"
    assert note.appointment.on == date(2026, 9, 24)
    assert (note.appointment.start, note.appointment.end) == (time(13, 0), time(14, 0))
    assert note.appointment.duration_minutes == 60
    assert note.appointment.billing_code == "90834"
    assert note.appointment.billing_description == "Psychotherapy, 45 min"
    assert note.appointment.starts_at == datetime(2026, 9, 24, 13, 0, tzinfo=ET)
    assert [d.code for d in note.diagnoses] == ["F41.1", "F33.1", "F43.10"]
    assert note.diagnoses[1].description == "Major depressive disorder, recurrent, moderate"
    assert note.body.startswith("Client arrived on time and was oriented")
    assert note.body.endswith("No suicidal ideation, self-harm or violence reported.")
    assert "Created on" not in note.body
    assert "Page 1" not in note.body
    assert note.created_at == datetime(2026, 9, 26, 23, 30, tzinfo=ET)
    assert note.locked is False
    assert note.signed_by is None


def test_locked_note_signature_block(archive: SimplePracticeArchive) -> None:
    note = next(n for n in archive.notes if n.source_id == "1007869378")
    assert note.client_display_name == "Pablo A. Bear"
    assert note.client_dob == date(2025, 1, 1)
    assert note.locked is True
    assert note.signed_by == "Avery Provider"
    assert note.signed_at == datetime(2026, 9, 27, 10, 58, tzinfo=ET)
    assert note.body == "Pablo progress note"
    # The signing IP address is read past and never kept.
    assert "IP address" not in note.body
    assert "203.0.113" not in note.body


def test_same_name_clients_are_told_apart_by_the_client_line(
    archive: SimplePracticeArchive,
) -> None:
    pablo_notes = [
        n
        for n in archive.notes
        if n.path.split("/")[1] == "Pablo Bear" and n.kind in {"progress", "psychotherapy"}
    ]
    by_name = {n.client_display_name for n in pablo_notes}
    assert by_name == {"Pablo A. Bear", "Pablo Bear"}
    assert {n.client_dob for n in pablo_notes if n.client_display_name == "Pablo A. Bear"} == {
        date(2025, 1, 1)
    }
    assert {n.client_dob for n in pablo_notes if n.client_display_name == "Pablo Bear"} == {
        date(2025, 3, 1)
    }


def test_administrative_note_name_disagrees_with_its_dob(archive: SimplePracticeArchive) -> None:
    """Administrative notes print first and last name only, even for a client
    whose display name carries a middle initial everywhere else — so the name
    on this note says one Pablo Bear and the DOB says the other. Attribution
    by name alone would mis-file it; the DOB line has to win."""
    admin = next(n for n in archive.notes if n.kind == "administrative")
    assert admin.client_display_name == "Pablo Bear"
    assert admin.client_dob == date(2025, 1, 1)  # the client whose notes say "Pablo A. Bear"


def test_chart_note_has_noted_at_and_no_appointment(archive: SimplePracticeArchive) -> None:
    chart = next(n for n in archive.notes if n.kind == "chart")
    assert chart.appointment is None
    assert chart.noted_at == datetime(2026, 9, 26, 23, 30, tzinfo=ET)
    assert (
        chart.body
        == "Client left a voicemail asking to move next week's appointment to the afternoon."
    )


def test_administrative_note_has_no_provider_line(archive: SimplePracticeArchive) -> None:
    admin = next(n for n in archive.notes if n.kind == "administrative")
    assert admin.client_display_name == "Pablo Bear"
    assert admin.client_dob == date(2025, 1, 1)
    assert admin.provider_name is None
    assert admin.appointment is None
    assert admin.body == "Admin note about pablo"


def test_treatment_plan_lists_ranked_diagnoses(archive: SimplePracticeArchive) -> None:
    plan = next(n for n in archive.notes if n.kind == "treatment_plan")
    assert plan.source_id == "47282569"
    assert [d.code for d in plan.diagnoses] == ["F41.1", "F33.1", "F43.10"]


def test_psychotherapy_notes_share_the_visit_with_their_progress_note(
    archive: SimplePracticeArchive,
) -> None:
    progress = {
        (n.client_display_name, n.appointment.starts_at)
        for n in archive.notes
        if n.kind == "progress" and n.appointment
    }
    psycho = {
        (n.client_display_name, n.appointment.starts_at)
        for n in archive.notes
        if n.kind == "psychotherapy" and n.appointment
    }
    assert psycho == progress


def test_questionnaires(archive: SimplePracticeArchive) -> None:
    qs = sorted(archive.questionnaires, key=lambda q: q.source_id)
    assert [q.source_id for q in qs] == ["128104770", "128104771"]
    first = qs[0]
    assert first.instrument == "gad7"
    assert first.title == "GAD-7"
    assert first.client_display_name == "Lulu L. Llama"
    assert (first.total_score, first.severity) == (16, "Severe")
    assert [i.score for i in first.items] == [2, 3, 2, 3, 3, 0, 3]
    assert first.items[0].question == "Feeling nervous, anxious, or on edge."
    assert first.items[1].response == "Nearly every day"
    assert all(i.max_score == 3 for i in first.items)
    assert first.completed_at == datetime(2026, 9, 26, 23, 30, tzinfo=ET)
    assert (qs[1].total_score, qs[1].severity) == (14, "Moderate")


def test_billing_documents(archive: SimplePracticeArchive) -> None:
    docs = {d.source_id: d for d in archive.billing}
    assert sorted(d.kind for d in docs.values()) == ["invoice"] * 3 + ["statement", "superbill"]
    invoice = docs["invoice:Lulu Llama/INV 1"]
    assert invoice.number == "1"
    assert invoice.issued_on == date(2026, 9, 24)
    assert invoice.client_display_name == "Lulu L. Llama"
    assert invoice.client_email == "lulu.llama@example.com"
    assert invoice.client_phone == "5555550100"
    assert invoice.provider_name == "Avery Provider"
    assert invoice.provider_email == "provider@example.com"
    assert (invoice.total_cents, invoice.balance_cents) == (10000, 10000)

    superbill = docs["superbill:Lulu Llama/SB 0001"]
    assert superbill.client_dob == date(1990, 3, 14)
    assert len(superbill.service_lines) == 1
    line = superbill.service_lines[0]
    assert (line.on, line.code, line.dx_pointer, line.units) == (date(2026, 9, 24), "90834", "1", 1)
    assert (line.fee_cents, line.paid_cents) == (10000, 0)

    statement = docs["statement:Lulu Llama/STMT 0001"]
    assert statement.balance_cents == 20000


def test_message_thread(archive: SimplePracticeArchive) -> None:
    assert len(archive.threads) == 1
    thread = archive.threads[0]
    assert thread.source_id == "Avery-Provider-Pablo-A.-Bear"
    assert thread.participants == ("System", "Pablo A. Bear")
    assert [m.sender for m in thread.messages] == ["System", "Pablo A. Bear"]
    assert thread.messages[1].body == "Hi, is this where I send my forms?"
    assert thread.messages[1].sent_at == datetime(2026, 9, 26, 23, 55, tzinfo=ET)


def test_uploads_are_opaque_and_keyed_by_path(archive: SimplePracticeArchive) -> None:
    ups = sorted(archive.uploads, key=lambda u: u.path)
    assert [u.original_filename for u in ups] == ["Another upload.txt", "Sample upload.txt"]
    assert {u.client_folder for u in ups} == {"Pablo Bear"}
    assert {u.ordinal for u in ups} == {1}


def test_provider_names_and_client_folders(archive: SimplePracticeArchive) -> None:
    assert archive.provider_names == ("Avery Provider",)
    assert archive.client_folders == ("Lulu Llama", "Pablo Bear")


def test_no_dob_capture_has_nothing_to_tell_the_clients_apart(
    no_dob_archive: SimplePracticeArchive,
) -> None:
    assert no_dob_archive.unrecognized == ()
    assert no_dob_archive.unreadable == ()
    assert {c.display_name for c in no_dob_archive.contacts} == {"Pablo Bear"}
    assert all(c.birthday is None for c in no_dob_archive.contacts)
    assert {n.client_display_name for n in no_dob_archive.notes} == {"Pablo Bear"}
    assert all(n.client_dob is None for n in no_dob_archive.notes)
    assert sorted(n.kind for n in no_dob_archive.notes) == ["progress", "psychotherapy"]


def test_reading_is_layout_driven_not_body_driven(tmp_path: Path) -> None:
    """A note is identified by its header and file name; nothing in the body
    changes what the reader records about it."""
    src = (
        FIXTURE
        / "Medical Records"
        / "Lulu Llama"
        / "Progress Note 2026-09-24 130000 1007836363.pdf"
    )
    target = tmp_path / "Medical Records" / "Lulu Llama" / src.name
    target.parent.mkdir(parents=True)
    target.write_bytes(src.read_bytes())
    archive = read_simplepractice_archive(tmp_path)
    assert len(archive.notes) == 1
    note = archive.notes[0]
    assert note.source_id == "1007836363"
    assert note.client_display_name == "Lulu L. Llama"
    assert note.appointment is not None
    assert note.appointment.billing_code == "90834"
