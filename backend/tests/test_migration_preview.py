# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The preview over the captured fixture: what would land, what is asked,
and how a ledger snapshot turns a second run into a delta."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from backend.app.migration.ledger import LedgerEntry
from backend.app.migration.preview import (
    ExistingPatient,
    PreviewInputs,
    build_preview,
    decisions_complete,
)
from backend.app.migration.readers.simplepractice import (
    SimplePracticeArchive,
    read_simplepractice_archive,
)

FIXTURE = Path(__file__).parent / "fixtures" / "simplepractice_export"
LULU, PABLO_A, PABLO = "112092152", "112092194", "112093920"


@pytest.fixture(scope="module")
def archive() -> SimplePracticeArchive:
    return read_simplepractice_archive(FIXTURE)


def _preview(
    archive: SimplePracticeArchive,
    *,
    ledger: dict[tuple[str, str], LedgerEntry] | None = None,
    existing_patients: list[ExistingPatient] | None = None,
    edited_targets: set[tuple[str, str]] | None = None,
) -> dict:
    inputs = PreviewInputs(
        ledger=ledger or {},
        existing_patients=existing_patients or [],
        edited_targets=edited_targets or set(),
    )
    return build_preview(archive, inputs)


def test_first_run_is_all_new(archive: SimplePracticeArchive) -> None:
    p = _preview(archive)
    assert p["source_system"] == "simplepractice"
    assert [c["card_id"] for c in p["clients"]] == [LULU, PABLO_A, PABLO]
    assert [c["card_id"] for c in p["non_client_contacts"]] == ["112092153"]
    assert p["providers"] == ["Avery Provider"]
    states = {(r["record_type"], r["state"]) for r in p["records"]}
    assert states == {(t, "new") for t in ("note", "questionnaire", "thread", "upload", "billing")}
    assert p["counts"]["note"] == {"new": 15}
    assert p["counts"]["upload"] == {"new": 2, "unresolved": 2}
    assert p["counts"]["contact"] == {"new": 3}


def test_preview_carries_no_content(archive: SimplePracticeArchive) -> None:
    text = json.dumps(_preview(archive))
    assert "Client arrived on time" not in text
    assert "Hi, is this where I send my forms" not in text
    assert "Pablo progress note" not in text


def test_questions_on_a_first_run(archive: SimplePracticeArchive) -> None:
    q = _preview(archive)["questions"]
    assert q["providers"] == [{"name": "Avery Provider"}]
    assert q["duplicates"] == []
    (group,) = q["same_name"]
    assert group["folder_name"] == "Pablo Bear"
    assert group["candidates"] == [PABLO_A, PABLO]
    assert {r["record_type"] for r in group["records"]} == {"upload"}
    assert len(group["records"]) == 2


def test_cannot_land_lists_billing_contacts_and_codes(archive: SimplePracticeArchive) -> None:
    what = {c["what"]: c["count"] for c in _preview(archive)["cannot_land"]}
    assert what["billing"] == 5
    assert what["non_client_contact"] == 1
    assert what["diagnosis_codes"] == 5  # Lulu's 4 visit notes + the treatment plan
    assert what["billing_codes"] == 12


def test_practice_proposals_come_from_the_archive(archive: SimplePracticeArchive) -> None:
    practice = _preview(archive)["practice"]
    assert practice["proposals"] == {
        "provider_name": "Avery Provider",
        "visit_kind": "Individual",
        "visit_minutes": 50,
        "rate_cents": 10000,
    }
    assert "npi" in practice["not_in_export"]


def test_decisions_complete_names_every_open_question(archive: SimplePracticeArchive) -> None:
    p = _preview(archive)
    missing = decisions_complete(p, {})
    assert "user for provider Avery Provider" in missing
    assert any(m.startswith("assignment for upload:") for m in missing)
    answered = {
        "providers": {"Avery Provider": "me"},
        "assignments": {
            f"upload:{r['source_id']}": PABLO_A for r in p["questions"]["same_name"][0]["records"]
        },
    }
    assert decisions_complete(p, answered) == []


def test_existing_patient_with_matching_dob_is_merged_silently(
    archive: SimplePracticeArchive,
) -> None:
    existing = [ExistingPatient("p-1", "Lulu", "Llama", date(1990, 3, 14), None)]
    p = _preview(archive, existing_patients=existing)
    lulu = next(c for c in p["clients"] if c["card_id"] == LULU)
    assert lulu["existing_patient_id"] == "p-1"
    assert lulu["match_evidence"] == "name_and_dob"
    assert p["questions"]["duplicates"] == []


def test_existing_patient_with_same_name_and_no_dob_is_a_question(
    archive: SimplePracticeArchive,
) -> None:
    existing = [ExistingPatient("p-2", "lulu", "LLAMA", None, None)]
    p = _preview(archive, existing_patients=existing)
    lulu = next(c for c in p["clients"] if c["card_id"] == LULU)
    assert lulu["existing_patient_id"] is None
    assert lulu["possible_duplicates"] == ["p-2"]
    assert p["questions"]["duplicates"] == [{"card_id": LULU, "possible_duplicates": ["p-2"]}]
    assert "duplicate decision for client 112092152" in decisions_complete(p, {})


def test_second_run_reads_as_a_delta(archive: SimplePracticeArchive) -> None:
    note = next(n for n in archive.notes if n.source_id == "1007836363")
    other = next(n for n in archive.notes if n.source_id == "1007836365")
    ledger = {
        ("note", note.source_id): LedgerEntry(
            "note", note.source_id, "notes", "n-1", note.digest, "landed", "r-1"
        ),
        ("note", other.source_id): LedgerEntry(
            "note", other.source_id, "notes", "n-2", "stale", "landed", "r-1"
        ),
        ("contact", LULU): LedgerEntry("contact", LULU, "patients", "p-1", "x", "landed", "r-1"),
    }
    p = _preview(archive, ledger=ledger, edited_targets={("notes", "n-2")})
    by_id = {r["source_id"]: r["state"] for r in p["records"] if r["record_type"] == "note"}
    assert by_id[note.source_id] == "unchanged"
    assert by_id[other.source_id] == "conflict"
    assert p["counts"]["note"]["new"] == 13
    lulu = next(c for c in p["clients"] if c["card_id"] == LULU)
    assert lulu["existing_patient_id"] == "p-1"
    assert lulu["match_evidence"] == "ledger"
