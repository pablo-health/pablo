# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Uploaded documents and submitted intake forms in the export archive.

Which documents go in is the selector's rule; these tests hold the archive
to it and to its own description of each file: the bytes under
``documents/`` are the stored bytes, and ``patient.json`` and the manifest
say so with a matching size and checksum.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest
from app.models import DocumentCategory, Patient, PatientDocument
from app.services import ExportService
from app.services.export_archive import document_archive_path
from jsonschema import Draft202012Validator

_T0 = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)


def _patient() -> Patient:
    return Patient(
        id="patient-123",
        first_name="Robin",
        last_name="Ash",
        session_count=0,
        created_at=_T0,
        updated_at=_T0,
    )


def _upload(
    category: DocumentCategory, *, filename: str = "file.pdf", by_patient: bool = False
) -> PatientDocument:
    document_id = f"doc-{category.value}"
    return PatientDocument(
        id=document_id,
        patient_id="patient-123",
        user_id=None if by_patient else "user-456",
        uploaded_by_patient_id="patient-123" if by_patient else None,
        filename=filename,
        mime_type="application/pdf",
        gcs_path=f"default/{category.value}/{document_id}",
        size_bytes=1,
        created_at=_T0,
        finalized_at=_T0,
        category=category,
    )


def _bytes_of(document: PatientDocument) -> bytes:
    return f"%PDF-1.4 stored bytes of {document.id}".encode()


@pytest.fixture
def documents() -> Mock:
    service = Mock()
    service.list_for_patient.return_value = [
        _upload(DocumentCategory.CHART, filename="labs.pdf"),
        _upload(DocumentCategory.CONSENT),
        _upload(DocumentCategory.INTAKE_ARTIFACT, by_patient=True),
        _upload(DocumentCategory.MESSAGE, by_patient=True),
        _upload(DocumentCategory.THERAPIST_PRIVATE),
        _upload(DocumentCategory.PSYCHOTHERAPY_NOTES),
    ]
    service.read_file.side_effect = _bytes_of
    return service


@pytest.fixture
def intake_forms() -> Mock:
    return Mock(return_value=[("assignment-1", b"<!DOCTYPE html><title>Intake</title>")])


@pytest.fixture
def service(documents: Mock, intake_forms: Mock) -> ExportService:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = _patient()
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []
    return ExportService(patients, sessions, notes, documents=documents, intake_forms=intake_forms)


def _export(service: ExportService, **options: bool) -> tuple[dict[str, Any], dict[str, bytes]]:
    result = service.get_patient_export_data("patient-123", "user-456", "zip", **options)
    with zipfile.ZipFile(io.BytesIO(result["content"])) as archive:
        return result, {name: archive.read(name) for name in archive.namelist()}


def _document_paths(files: dict[str, bytes]) -> set[str]:
    return {name for name in files if name.startswith("documents/")}


def test_the_default_copy_carries_every_record_category_and_neither_restricted_one(
    service: ExportService,
) -> None:
    result, files = _export(service)

    assert _document_paths(files) == {
        "documents/doc-chart__labs.pdf",
        "documents/doc-consent__file.pdf",
        "documents/doc-intake_artifact__file.pdf",
        "documents/doc-message__file.pdf",
    }
    assert [d.id for d in result["documents"]] == [
        "doc-chart",
        "doc-consent",
        "doc-intake_artifact",
        "doc-message",
    ]


def test_psychotherapy_notes_join_with_the_option_and_therapist_private_never_does(
    service: ExportService, documents: Mock
) -> None:
    _, files = _export(service, include_psychotherapy_notes=True)

    paths = _document_paths(files)
    assert "documents/doc-psychotherapy_notes__file.pdf" in paths
    assert not any("therapist_private" in path for path in paths)
    read = [c.args[0].id for c in documents.read_file.call_args_list]
    assert "doc-therapist_private" not in read, "an excluded file is never fetched"


def test_each_file_is_the_stored_bytes_and_patient_json_describes_it(
    service: ExportService,
) -> None:
    _, files = _export(service, include_psychotherapy_notes=True)
    document = json.loads(files["patient.json"])
    Draft202012Validator(json.loads(files["schema.json"])).validate(document)

    entries = {entry["id"]: entry for entry in document["documents"]}
    assert set(entries) == {
        "doc-chart",
        "doc-consent",
        "doc-intake_artifact",
        "doc-message",
        "doc-psychotherapy_notes",
    }
    for entry in entries.values():
        data = files[entry["archive_path"]]
        assert data == f"%PDF-1.4 stored bytes of {entry['id']}".encode()
        assert entry["bytes"] == len(data)
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["content_type"] == "application/pdf"
        assert datetime.fromisoformat(entry["uploaded_at"]) == _T0
    assert entries["doc-chart"]["filename"] == "labs.pdf"
    assert entries["doc-chart"]["category"] == "chart"
    assert entries["doc-chart"]["uploaded_by"] == "clinician"
    assert entries["doc-message"]["uploaded_by"] == "patient"


def test_the_manifest_lists_documents_and_forms_by_kind(service: ExportService) -> None:
    _, files = _export(service)
    manifest = json.loads(files["manifest.json"])

    listed = {entry["path"]: entry for entry in manifest["files"]}
    assert set(listed) == set(files) - {"manifest.json"}
    for path, entry in listed.items():
        assert entry["sha256"] == hashlib.sha256(files[path]).hexdigest(), path
    assert {p for p, e in listed.items() if e["kind"] == "document"} == _document_paths(files)
    assert {p for p, e in listed.items() if e["kind"] == "intake_form"} == {
        "intake/assignment-1.html"
    }


def test_submitted_forms_are_carried_as_the_intake_export_renders_them(
    service: ExportService, intake_forms: Mock
) -> None:
    result, files = _export(service)

    assert files["intake/assignment-1.html"] == b"<!DOCTYPE html><title>Intake</title>"
    assert result["intake_assignment_ids"] == ["assignment-1"]
    patient, user_id, exported_at = intake_forms.call_args.args
    assert patient.id == "patient-123"
    assert user_id == "user-456"
    assert datetime.fromisoformat(json.loads(files["patient.json"])["exported_at"]) == exported_at


def test_json_and_pdf_read_no_files(
    service: ExportService, documents: Mock, intake_forms: Mock
) -> None:
    service.get_patient_export_data("patient-123", "user-456", "json")
    service.get_patient_export_data("patient-123", "user-456", "pdf")

    documents.list_for_patient.assert_not_called()
    documents.read_file.assert_not_called()
    intake_forms.assert_not_called()


def test_without_a_document_reader_the_archive_carries_no_documents() -> None:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = _patient()
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []
    _, files = _export(ExportService(patients, sessions, notes))

    assert json.loads(files["patient.json"])["documents"] == []
    assert _document_paths(files) == set()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("labs.pdf", "documents/doc-chart__labs.pdf"),
        ("../../etc/passwd", "documents/doc-chart__.._.._etc_passwd"),
        ("scan\\front.png", "documents/doc-chart__scan_front.png"),
        ("line\nbreak.pdf", "documents/doc-chart__line_break.pdf"),
        ("   ", "documents/doc-chart__document"),
    ],
)
def test_an_uploaded_name_cannot_leave_the_documents_directory(
    filename: str, expected: str
) -> None:
    assert document_archive_path(_upload(DocumentCategory.CHART, filename=filename)) == expected
