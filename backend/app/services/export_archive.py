# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient export archive: one ZIP a person, another system or a script can use.

``chart.pdf`` is the chart to read, ``patient.json`` the same chart as data,
``schema.json`` the JSON Schema that data follows, ``documents/`` the files
uploaded to the chart as they were uploaded, ``intake/`` each submitted form
as its own document, ``manifest.json`` every other file with its size and
SHA-256, and ``README.txt`` says which is which.

Built in memory: one client's archive is small. The archive is deterministic
for the same rows and the same export time (entries carry the export time,
not the wall clock), so two builds of one export are the same bytes.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import TYPE_CHECKING, cast

from ..models.export import (
    DocumentReference,
    Encounter,
    ExportDocument,
    ExportDocumentCategory,
    ExportManifest,
    ExportPatient,
    ExportTranscript,
    ManifestFile,
    ManifestFileKind,
    PatientExportDocument,
    Practitioner,
)
from .export_pdf import final_content

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from ..models import Note, Patient, PatientDocument, TherapySession
    from ..repositories.clinician_profile import ClinicianProfile
    from .record_set import RecordSetSelector

README = """\
This archive is one client's chart, exported from Pablo.
chart.pdf      The chart as a document to read or print.
patient.json   The same chart as structured data.
schema.json    The JSON Schema that patient.json follows.
documents/     Files uploaded to the chart, as they were uploaded.
intake/        Each submitted intake form, as a document to read or print.
manifest.json  Every other file in this archive, with its size and SHA-256 checksum.
README.txt     This file.

The format is documented in docs/reference/export-format.md in the Pablo source.
"""

#: Characters an uploaded filename may not carry into an archive entry name:
#: path separators, which would move the entry out of its directory, and
#: control characters, which some unzip tools print raw.
_UNSAFE_IN_ENTRY_NAME = re.compile(r"[\x00-\x1f\x7f/\\]")


@dataclass(frozen=True)
class ArchiveFile:
    """A file carried in the archive beside the chart, as its own bytes."""

    path: str
    kind: ManifestFileKind
    data: bytes


def document_archive_path(document: PatientDocument) -> str:
    """``documents/<id>__<filename>``: unique by id, recognisable by name."""
    name = _UNSAFE_IN_ENTRY_NAME.sub("_", document.filename).strip() or "document"
    return f"documents/{document.id}__{name}"


def intake_form_archive_path(assignment_id: str) -> str:
    return f"intake/{assignment_id}.html"


def export_document(document: PatientDocument, data: bytes) -> ExportDocument:
    """The ``patient.json`` entry for one uploaded file, measured from its bytes.

    Size and checksum come from the bytes that go into the archive, not from
    the row, so the entry describes the copy the reader holds.
    """
    return ExportDocument(
        id=document.id,
        # The selector admitted it, so it is one of the categories a copy carries.
        category=cast("ExportDocumentCategory", document.category.value),
        filename=document.filename,
        content_type=document.mime_type,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        uploaded_at=document.finalized_at or document.created_at,
        uploaded_by="patient" if document.uploaded_by == "patient" else "clinician",
        archive_path=document_archive_path(document),
    )


def practitioner_from(
    billing_profile: Mapping[str, object], clinician: ClinicianProfile | None
) -> Practitioner:
    """The practice's billing identity, with the exporting clinician's taxonomy.

    The billing profile carries the name and NPI the practice bills under but
    no taxonomy; that lives on the clinician's own profile. A practice that
    has not filled in its billing profile still gets the clinician's NPI.
    """
    legal_name = billing_profile.get("legal_name")
    billing_npi = billing_profile.get("billing_npi")
    npi = billing_npi if isinstance(billing_npi, str) and billing_npi else None
    if npi is None and clinician is not None:
        npi = clinician.npi_number
    return Practitioner(
        name=legal_name if isinstance(legal_name, str) else None,
        npi=npi,
        taxonomy_code=clinician.taxonomy_code if clinician else None,
    )


def _document_reference(note: Note) -> DocumentReference:
    return DocumentReference(
        id=note.id,
        note_type=note.note_type,
        restricted=note.restricted,
        content=note.content,
        content_edited=note.content_edited,
        final_content=final_content(note),
        was_edited=bool(note.content_edited),
        created_at=note.created_at,
        finalized_at=note.finalized_at,
    )


def _encounter(
    session: TherapySession, note: Note | None, selector: RecordSetSelector
) -> Encounter:
    transcript = selector.transcript_for(session)
    return Encounter(
        id=session.id,
        session_number=session.session_number,
        session_date=session.session_date,
        status=session.status,
        created_at=session.created_at,
        transcript=(
            ExportTranscript(format=transcript.format, content=transcript.content)
            if transcript is not None
            else None
        ),
        document_reference=_document_reference(note) if note else None,
    )


def build_export_document(
    patient: Patient,
    practitioner: Practitioner,
    sessions: list[TherapySession],
    notes_by_session: dict[str, Note | None],
    standalone_notes: list[Note],
    documents: list[ExportDocument],
    exported_at: datetime,
    selector: RecordSetSelector,
) -> PatientExportDocument:
    return PatientExportDocument(
        exported_at=exported_at,
        options=selector.options,
        patient=ExportPatient(
            identifier=patient.id,
            first_name=patient.first_name,
            last_name=patient.last_name,
            birth_date=patient.date_of_birth,
            sex=patient.sex,
            email=patient.email,
            phone=patient.phone,
            address_line1=patient.address_line1,
            address_line2=patient.address_line2,
            city=patient.city,
            state=patient.state,
            postal_code=patient.postal_code,
            status=patient.status,
            diagnosis=patient.diagnosis,
            chart_closed_at=patient.chart_closed_at,
            created_at=patient.created_at,
            updated_at=patient.updated_at,
        ),
        practitioner=practitioner,
        sessions=[_encounter(s, notes_by_session.get(s.id), selector) for s in sessions],
        standalone_notes=[_document_reference(n) for n in standalone_notes],
        documents=documents,
    )


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def build_archive(
    document: PatientExportDocument,
    chart_pdf: bytes,
    files: Sequence[ArchiveFile] = (),
) -> bytes:
    """The ZIP: the described files, then the manifest that describes them.

    ``files`` are the uploaded documents and intake forms, each at the path
    ``patient.json`` or the caller gave it.
    """
    described = [
        ArchiveFile("chart.pdf", "pdf", chart_pdf),
        ArchiveFile("patient.json", "json", _json_bytes(document.model_dump(mode="json"))),
        ArchiveFile(
            "schema.json", "schema", _json_bytes(PatientExportDocument.model_json_schema())
        ),
        ArchiveFile("README.txt", "text", README.encode()),
        *files,
    ]
    manifest = ExportManifest(
        exported_at=document.exported_at,
        options=document.options,
        files=[
            ManifestFile(
                path=f.path,
                bytes=len(f.data),
                sha256=hashlib.sha256(f.data).hexdigest(),
                kind=f.kind,
            )
            for f in described
        ],
    )
    entries = [
        *((f.path, f.data) for f in described),
        ("manifest.json", _json_bytes(manifest.model_dump(mode="json"))),
    ]

    stamp = document.exported_at.timetuple()[:6]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, data in entries:
            archive.writestr(zipfile.ZipInfo(path, date_time=stamp), data, zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
