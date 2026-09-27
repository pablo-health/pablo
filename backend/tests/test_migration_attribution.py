# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Attribution over the captured fixtures: every record either lands on the
one client the evidence names, or is left for the practice to assign."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.migration.attribution import ArchiveAttribution, attribute_archive
from backend.app.migration.readers.simplepractice import read_simplepractice_archive

FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_export"
NO_DOB_FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_export_same_name_no_dob"

LULU, LOLA, PABLO_A, PABLO = "112092152", "112092153", "112092194", "112093920"


@pytest.fixture(scope="module")
def attributed() -> ArchiveAttribution:
    return attribute_archive(read_simplepractice_archive(FIXTURE))


@pytest.fixture(scope="module")
def no_dob() -> ArchiveAttribution:
    return attribute_archive(read_simplepractice_archive(NO_DOB_FIXTURE))


def test_clients_and_other_contacts(attributed: ArchiveAttribution) -> None:
    assert sorted(c.source_id for c in attributed.clients) == [LULU, PABLO_A, PABLO]
    assert [c.source_id for c in attributed.non_client_contacts] == [LOLA]
    assert set(attributed.same_name_groups) == {"Pablo Bear"}


def test_single_client_records_need_no_decision(attributed: ArchiveAttribution) -> None:
    lulu = [a for a in attributed.attributions.values() if "Lulu Llama" in a.path]
    # 2 progress, 2 psychotherapy, chart note, plan, 2 GAD-7, 3 invoices, statement, superbill
    assert len(lulu) == 13
    assert all(a.card_id == LULU for a in lulu)
    assert {a.evidence for a in lulu} == {"only_candidate"}
    assert not any(a.name_disagrees for a in lulu)


def test_same_name_notes_are_decided_by_dob(attributed: ArchiveAttribution) -> None:
    notes = [a for a in attributed.by_type("note") if a.path.split("/")[1] == "Pablo Bear"]
    assert len(notes) == 9  # 4 progress, 4 psychotherapy, 1 administrative
    assert all(a.resolved for a in notes)
    assert {a.evidence for a in notes} == {"dob"}
    by_id = {a.source_id: a for a in notes}
    assert by_id["1007866847"].card_id == PABLO  # 09-23, DOB 03/01/2025
    assert by_id["1007869378"].card_id == PABLO_A  # 09-28 11:30, DOB 01/01/2025
    assert by_id["1007873720"].card_id == PABLO_A
    assert by_id["1007873575"].card_id == PABLO


def test_administrative_note_dob_wins_over_its_name(attributed: ArchiveAttribution) -> None:
    admin = attributed.attributions[("note", "14037546")]
    assert admin.card_id == PABLO_A
    assert admin.evidence == "dob"
    assert admin.name_disagrees is True  # the note prints "Pablo Bear", the other client's name


def test_message_thread_is_decided_by_sender_display_name(attributed: ArchiveAttribution) -> None:
    (thread,) = attributed.by_type("thread")
    assert thread.card_id == PABLO_A
    assert thread.evidence == "display_name"


def test_uploads_in_a_shared_folder_are_unresolved(attributed: ArchiveAttribution) -> None:
    uploads = attributed.by_type("upload")
    assert len(uploads) == 2
    assert all(not a.resolved for a in uploads)
    assert all(a.candidates == (PABLO_A, PABLO) for a in uploads)
    assert [a.source_id for a in attributed.unresolved] == sorted(a.source_id for a in uploads)


def test_without_birthdays_the_notes_are_unresolved_too(no_dob: ArchiveAttribution) -> None:
    assert set(no_dob.same_name_groups) == {"Pablo Bear"}
    assert len(no_dob.attributions) == 4  # 2 notes, 2 uploads
    assert all(not a.resolved for a in no_dob.attributions.values())
    assert all(len(a.candidates) == 2 for a in no_dob.attributions.values())
