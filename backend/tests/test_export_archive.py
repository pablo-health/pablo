# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The export archive: five files, a document that validates against the
schema shipped beside it, and a manifest whose checksums hold."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from app.models import Note, Patient, TherapySession, Transcript
from app.models.export import PatientExportDocument, Practitioner
from app.repositories.clinician_profile import ClinicianProfile
from app.services import ExportService
from app.services.export_archive import README, build_archive, practitioner_from
from jsonschema import Draft202012Validator

_T0 = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)


def _patient() -> Patient:
    return Patient(
        id="patient-123",
        first_name="Robin",
        last_name="Ash",
        date_of_birth="1980-01-15",
        session_count=1,
        created_at=_T0,
        updated_at=_T0,
    )


def _session() -> TherapySession:
    return TherapySession(
        id="session-1",
        user_id="user-456",
        patient_id="patient-123",
        session_date=_T0,
        session_number=1,
        status="finalized",
        transcript=Transcript(format="txt", content="Client: the week was long."),
        created_at=_T0,
    )


def _note(note_id: str, *, session_id: str | None, restricted: bool = False) -> Note:
    return Note(
        id=note_id,
        patient_id="patient-123",
        session_id=session_id,
        note_type="psychotherapy" if restricted else ("soap" if session_id else "narrative"),
        content={"note": {"body": f"body of {note_id}"}},
        created_at=_T0,
        updated_at=_T0,
        finalized_at=None if restricted else _T0,
        restricted=restricted,
    )


@pytest.fixture
def practitioner_loader() -> Mock:
    return Mock(
        return_value=Practitioner(
            name="Ash Counseling", npi="1234567893", taxonomy_code="101YM0800X"
        )
    )


@pytest.fixture
def service(practitioner_loader: Mock) -> ExportService:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = _patient()
    sessions.list_by_patient.return_value = [_session()]
    notes.list_by_patient.return_value = [
        _note("soap-1", session_id="session-1"),
        _note("narrative-1", session_id=None),
        _note("psychotherapy-1", session_id=None, restricted=True),
    ]
    return ExportService(patients, sessions, notes, practitioner=practitioner_loader)


def _unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _export(service: ExportService, **options: bool) -> tuple[dict[str, Any], dict[str, bytes]]:
    result = service.get_patient_export_data("patient-123", "user-456", "zip", **options)
    return result, _unzip(result["content"])


def test_zip_holds_the_five_files(service: ExportService) -> None:
    result, files = _export(service)

    assert result["content_type"] == "application/zip"
    assert result["filename"].startswith("patient_patient-123_export_")
    assert result["filename"].endswith(".zip")
    assert sorted(files) == [
        "README.txt",
        "chart.pdf",
        "manifest.json",
        "patient.json",
        "schema.json",
    ]
    assert files["chart.pdf"].startswith(b"%PDF")
    assert files["README.txt"].decode() == README


def test_readme_names_the_schema_version_patient_json_carries(service: ExportService) -> None:
    _, files = _export(service)
    version = json.loads(files["patient.json"])["schema_version"]
    assert f"Schema version: {version} " in files["README.txt"].decode()


def test_patient_json_validates_against_the_schema_beside_it(service: ExportService) -> None:
    for options in ({}, {"include_transcripts": True, "include_psychotherapy_notes": True}):
        _, files = _export(service, **options)
        schema = json.loads(files["schema.json"])
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(json.loads(files["patient.json"]))


def test_schema_json_is_the_model_schema_verbatim(service: ExportService) -> None:
    _, files = _export(service)
    assert json.loads(files["schema.json"]) == PatientExportDocument.model_json_schema()


def test_manifest_lists_every_other_file_with_a_matching_checksum(service: ExportService) -> None:
    _, files = _export(service, include_transcripts=True)
    manifest = json.loads(files["manifest.json"])

    assert manifest["schema_version"] == "1.0"
    assert manifest["options"] == {
        "include_transcripts": True,
        "include_psychotherapy_notes": False,
    }
    listed = {entry["path"]: entry for entry in manifest["files"]}
    assert set(listed) == set(files) - {"manifest.json"}
    for path, entry in listed.items():
        assert entry["bytes"] == len(files[path])
        assert entry["sha256"] == hashlib.sha256(files[path]).hexdigest()
    assert {p: e["kind"] for p, e in listed.items()} == {
        "chart.pdf": "pdf",
        "patient.json": "json",
        "schema.json": "schema",
        "README.txt": "text",
    }


def test_default_document_leaves_out_transcripts_and_psychotherapy_notes(
    service: ExportService, practitioner_loader: Mock
) -> None:
    _, files = _export(service)
    document = json.loads(files["patient.json"])

    assert document["schema_version"] == "1.0"
    assert document["options"] == {
        "include_transcripts": False,
        "include_psychotherapy_notes": False,
    }
    [encounter] = document["sessions"]
    assert "transcript" not in encounter, "absent, not null"
    assert encounter["document_reference"]["id"] == "soap-1"
    assert [n["id"] for n in document["standalone_notes"]] == ["narrative-1"]
    assert document["patient"]["identifier"] == "patient-123"
    assert document["patient"]["birth_date"] == "1980-01-15"
    assert document["practitioner"] == {
        "name": "Ash Counseling",
        "npi": "1234567893",
        "taxonomy_code": "101YM0800X",
    }
    practitioner_loader.assert_called_once_with("user-456")


def test_both_options_bring_in_the_transcript_and_the_psychotherapy_note(
    service: ExportService,
) -> None:
    _, files = _export(service, include_transcripts=True, include_psychotherapy_notes=True)
    document = json.loads(files["patient.json"])

    [encounter] = document["sessions"]
    assert encounter["transcript"] == {"format": "txt", "content": "Client: the week was long."}
    by_id = {n["id"]: n for n in document["standalone_notes"]}
    assert set(by_id) == {"narrative-1", "psychotherapy-1"}
    assert by_id["psychotherapy-1"]["restricted"] is True


def test_timestamps_carry_an_offset(service: ExportService) -> None:
    _, files = _export(service)
    document = json.loads(files["patient.json"])
    for stamp in (
        document["exported_at"],
        document["patient"]["created_at"],
        document["sessions"][0]["session_date"],
    ):
        assert datetime.fromisoformat(stamp).utcoffset() is not None, stamp


def test_a_stored_timestamp_without_a_zone_is_read_as_utc() -> None:
    naive = datetime(2024, 1, 15, 10, 0)
    document = PatientExportDocument.model_validate(
        {
            "exported_at": naive,
            "options": {},
            "patient": {
                "identifier": "p",
                "first_name": "A",
                "last_name": "B",
                "status": "active",
                "created_at": naive,
                "updated_at": naive,
            },
            "practitioner": {},
            "sessions": [],
            "standalone_notes": [],
            "documents": [],
        }
    )
    assert document.exported_at.tzinfo is UTC


def test_the_same_document_builds_the_same_bytes(service: ExportService) -> None:
    _, files = _export(service)
    document = PatientExportDocument.model_validate_json(files["patient.json"])
    assert build_archive(document, files["chart.pdf"]) == build_archive(
        document, files["chart.pdf"]
    )


def test_without_a_loader_the_practitioner_is_empty() -> None:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = _patient()
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []
    result = ExportService(patients, sessions, notes).get_patient_export_data(
        "patient-123", "user-456", "zip"
    )
    document = json.loads(_unzip(result["content"])["patient.json"])
    assert document["practitioner"] == {"name": None, "npi": None, "taxonomy_code": None}


class TestPractitionerFrom:
    def test_name_and_npi_from_the_billing_profile_taxonomy_from_the_clinician(self) -> None:
        clinician = ClinicianProfile(
            user_id="u", practice_id="p", npi_number="1111111112", taxonomy_code="101YM0800X"
        )
        practitioner = practitioner_from(
            {"legal_name": "Ash Counseling", "billing_npi": "1234567893"}, clinician
        )
        assert practitioner == Practitioner(
            name="Ash Counseling", npi="1234567893", taxonomy_code="101YM0800X"
        )

    def test_an_unset_billing_npi_falls_back_to_the_clinicians(self) -> None:
        clinician = ClinicianProfile(user_id="u", practice_id="p", npi_number="1111111112")
        practitioner = practitioner_from({"legal_name": None, "billing_npi": None}, clinician)
        assert practitioner == Practitioner(npi="1111111112")

    def test_nothing_set_is_nothing_claimed(self) -> None:
        assert practitioner_from({}, None) == Practitioner()
