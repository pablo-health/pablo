# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient export document: what ``patient.json`` in an export archive holds.

The model tree is the contract. ``schema.json`` in the same archive is this
tree's JSON Schema, written verbatim, so a consumer validates against the
exact shape it was given rather than against documentation.

Object names follow FHIR where that costs nothing (a session is an
``Encounter``, a note a ``DocumentReference``, the clinician a
``Practitioner``) so a FHIR bundle can be derived later, while JSON keys stay
snake_case. Every timestamp is ISO-8601 with an offset.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

SCHEMA_VERSION: Final = "1.0"


def _with_offset(value: datetime) -> datetime:
    """A stored timestamp without a zone is UTC; give it the offset that says so."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


Timestamp = Annotated[datetime, AfterValidator(_with_offset)]


class ExportOptions(BaseModel):
    """What the caller chose to include beyond the default record copy."""

    model_config = ConfigDict(frozen=True)

    include_transcripts: bool = False
    include_psychotherapy_notes: bool = False


class ExportPatient(BaseModel):
    """The client's demographics."""

    identifier: str = Field(description="Pablo's id for the client.")
    first_name: str
    last_name: str
    birth_date: str | None = Field(default=None, description="YYYY-MM-DD, when recorded.")
    sex: str | None = Field(default=None, description="Administrative sex: M, F or U.")
    email: str | None = None
    phone: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    status: str
    diagnosis: str | None = None
    chart_closed_at: Timestamp | None = None
    created_at: Timestamp
    updated_at: Timestamp


class Practitioner(BaseModel):
    """Who the record comes from, as the practice has set itself up to bill."""

    name: str | None = None
    npi: str | None = Field(default=None, description="National Provider Identifier.")
    taxonomy_code: str | None = Field(default=None, description="NUCC provider taxonomy code.")


class ExportTranscript(BaseModel):
    format: str
    content: str


class DocumentReference(BaseModel):
    """One note. ``final_content`` is what the clinician signed off on."""

    id: str
    note_type: str
    restricted: bool = Field(description="A psychotherapy note, readable by its author alone.")
    content: dict[str, Any] | None
    content_edited: dict[str, Any] | None
    final_content: dict[str, Any] | None
    was_edited: bool
    created_at: Timestamp
    finalized_at: Timestamp | None


class Encounter(BaseModel):
    """One session, with its note when it has one.

    ``transcript`` is absent, not null, when transcripts were not included,
    so an omitted transcript never reads as an empty one; the document's
    ``options`` say which it was.
    """

    id: str
    session_number: int
    session_date: Timestamp
    status: str
    created_at: Timestamp
    transcript: ExportTranscript | None = Field(default=None, exclude_if=lambda v: v is None)
    document_reference: DocumentReference | None


class PatientExportDocument(BaseModel):
    """One client's chart as structured data (``patient.json``)."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    exported_at: Timestamp
    options: ExportOptions
    patient: ExportPatient
    practitioner: Practitioner
    sessions: list[Encounter]
    standalone_notes: list[DocumentReference] = Field(
        description="Notes written without a session, such as an intake or narrative."
    )


ManifestFileKind = Literal["pdf", "json", "schema", "text"]


class ManifestFile(BaseModel):
    path: str
    bytes: int
    sha256: str
    kind: ManifestFileKind


class ExportManifest(BaseModel):
    """``manifest.json``: every other file in the archive, with its checksum."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    exported_at: Timestamp
    options: ExportOptions
    files: list[ManifestFile]
