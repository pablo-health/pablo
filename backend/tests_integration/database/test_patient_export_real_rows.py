# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The patient export on real rows: what is in it by default, and what is not.

The unit tests for the export are mocks all the way down (the route tests
mock the service, the service tests mock the repositories), which is how a
datetime 500 once shipped on this endpoint. This module runs the real
``ExportService`` over the real Postgres repositories in a provisioned
practice schema, as the clinician, with row security armed.

One chart, one clinician:

* a session with a transcript and its SOAP note;
* a narrative note written without a session;
* a psychotherapy note (``restricted``) by the same clinician.

Each piece of content carries a sentinel string found nowhere else, so an
absence assertion can only pass because the content really was left out. The
all-included case runs against the same rows and is the control: it proves
every sentinel is reachable, in JSON and in the PDF text, before any is
asserted missing.

The archive (``format=zip``) is unpacked and held to its own promises:
``patient.json`` validates against the ``schema.json`` shipped beside it,
and every checksum in ``manifest.json`` matches the file it names.

Run: ``make test-integration``.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from alembic import command
from alembic.config import Config
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine, text

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.models.export import Practitioner
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_db_url = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not _db_url or os.environ.get("DATABASE_BACKEND") != "postgres",
    reason=(
        "PostgreSQL not configured. Set DATABASE_URL and "
        "DATABASE_BACKEND=postgres; testcontainers should set both."
    ),
)

_CLINICIAN = "3b8e1f64-7a2d-5c90-b4e6-1d9f7a3c5e82"

_TRANSCRIPT_SENTINEL = "TRANSCRIPTSENTINEL7Q4Z"
_SOAP_SENTINEL = "SOAPSENTINEL2K8M"
_NARRATIVE_SENTINEL = "NARRATIVESENTINEL5R1T"
_RESTRICTED_SENTINEL = "PSYCHOTHERAPYSENTINEL9W3X"

_PRACTICE_NAME = "Harbor Light Counseling"
_NPI = "1234567893"
_TAXONOMY = "101YM0800X"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    backend_dir = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
    eng = create_engine(_db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def tenant_schema(engine: Engine) -> Iterator[str]:
    from app.db.provisioning import create_practice_schema  # noqa: PLC0415

    # Warm the pool so policy CREATEs referencing ``has_patient_access``
    # (which lives in ``practice``) resolve.
    with engine.connect() as conn:
        conn.execute(text("SET search_path = practice, platform, public"))
        conn.commit()

    schema = f"practice_test_export_{uuid.uuid4().hex[:8]}"
    create_practice_schema(engine, schema)
    yield schema
    with engine.connect() as conn:
        conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        conn.commit()


@pytest.fixture(scope="module")
def chart(engine: Engine, tenant_schema: str) -> dict[str, str]:
    """Seed the chart through the real repositories and return its ids."""
    from app.models import Note, TherapySession, Transcript  # noqa: PLC0415
    from app.repositories.clinician_profile import ClinicianProfile  # noqa: PLC0415
    from app.repositories.postgres.clinician_profile import (  # noqa: PLC0415
        PostgresClinicianProfileRepository,
    )
    from app.repositories.postgres.note import PostgresNotesRepository  # noqa: PLC0415
    from app.repositories.postgres.session import (  # noqa: PLC0415
        PostgresTherapySessionRepository,
    )
    from app.services.practice_billing_profile import update_billing_profile  # noqa: PLC0415

    patient_id = _seed_patient(engine, tenant_schema)
    now = datetime.now(UTC).replace(microsecond=0)
    ids = {
        "patient": patient_id,
        "session": str(uuid.uuid4()),
        "soap": str(uuid.uuid4()),
        "narrative": str(uuid.uuid4()),
        "psychotherapy": str(uuid.uuid4()),
    }

    session, tokens = _open_tenant_session(engine, tenant_schema)
    try:
        PostgresTherapySessionRepository(session).create(
            TherapySession(
                id=ids["session"],
                user_id=_CLINICIAN,
                patient_id=patient_id,
                session_date=now,
                session_number=1,
                status="finalized",
                transcript=Transcript(
                    format="txt",
                    content=f"Therapist: How was the week?\nClient: {_TRANSCRIPT_SENTINEL}",
                ),
                created_at=now,
            )
        )
        notes = PostgresNotesRepository(session)
        notes.add(
            Note(
                id=ids["soap"],
                patient_id=patient_id,
                session_id=ids["session"],
                note_type="soap",
                content={
                    "subjective": {
                        "chief_complaint": "",
                        "mood_affect": "",
                        "symptoms": [],
                        "client_narrative": _SOAP_SENTINEL,
                    },
                    "objective": {},
                    "assessment": {},
                    "plan": {},
                },
                created_at=now,
                updated_at=now,
                finalized_at=now,
                author_user_id=_CLINICIAN,
            ),
            _CLINICIAN,
        )
        notes.add(
            Note(
                id=ids["narrative"],
                patient_id=patient_id,
                note_type="narrative",
                content={"note": {"body": _NARRATIVE_SENTINEL}},
                created_at=now,
                updated_at=now,
                finalized_at=now,
                author_user_id=_CLINICIAN,
            ),
            _CLINICIAN,
        )
        PostgresClinicianProfileRepository(session).create(
            ClinicianProfile(
                user_id=_CLINICIAN,
                practice_id=tenant_schema,
                npi_number="1111111112",
                taxonomy_code=_TAXONOMY,
            )
        )
        update_billing_profile(session, {"legal_name": _PRACTICE_NAME, "billing_npi": _NPI})
        notes.add(
            Note(
                id=ids["psychotherapy"],
                patient_id=patient_id,
                note_type="psychotherapy",
                content={"note": {"body": _RESTRICTED_SENTINEL}},
                created_at=now,
                updated_at=now,
                author_user_id=_CLINICIAN,
                restricted=True,
            ),
            _CLINICIAN,
        )
        session.commit()
    finally:
        _close_tenant_session(session, tokens)
    return ids


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def _seed_patient(engine: Engine, tenant_schema: str) -> str:
    patient_id = str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(
            text("SELECT set_config('app.current_user_id', :u, false)"),
            {"u": _CLINICIAN},
        )
        conn.execute(
            text(
                "INSERT INTO patients (id, first_name, last_name, "
                "first_name_lower, last_name_lower, status, "
                "session_count, created_at, updated_at) "
                "VALUES (CAST(:pid AS uuid), 'Export', 'Patient', "
                "'export', 'patient', 'active', 1, now(), now())"
            ),
            {"pid": patient_id},
        )
        conn.execute(
            text(
                "INSERT INTO patient_clinicians (patient_id, user_id, granted_by) "
                "VALUES (CAST(:pid AS uuid), :u, :u)"
            ),
            {"pid": patient_id, "u": _CLINICIAN},
        )
    return patient_id


def _open_tenant_session(engine: Engine, tenant_schema: str) -> tuple[Session, tuple[Any, Any]]:
    """An ORM session on the practice schema, armed as the clinician."""
    from app.db import (  # noqa: PLC0415
        _current_tenant_schema,
        _current_user_id,
        arm_current_user_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    tokens = (_current_tenant_schema.set(tenant_schema), _current_user_id.set(_CLINICIAN))
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_user_id(session, _CLINICIAN)
    return session, tokens


def _close_tenant_session(session: Session, tokens: tuple[Any, Any]) -> None:
    from app.db import _current_tenant_schema, _current_user_id  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(tokens[0])
    _current_user_id.reset(tokens[1])


def _export(
    engine: Engine,
    tenant_schema: str,
    patient_id: str,
    export_format: str,
    **options: bool,
) -> dict[str, Any]:
    """Run the real service as the clinician; ``options`` are its two flags.

    Wired the way the route wires it: the practitioner comes from the
    practice's billing profile and the clinician's profile in the same
    tenant session, and notes are labelled by the built-in note types.
    """
    from app.notes import NoteTypeRegistry, register_builtin_note_types  # noqa: PLC0415
    from app.repositories.postgres.clinician_profile import (  # noqa: PLC0415
        PostgresClinicianProfileRepository,
    )
    from app.repositories.postgres.note import PostgresNotesRepository  # noqa: PLC0415
    from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415
    from app.repositories.postgres.session import (  # noqa: PLC0415
        PostgresTherapySessionRepository,
    )
    from app.services import ExportService  # noqa: PLC0415
    from app.services.export_archive import practitioner_from  # noqa: PLC0415
    from app.services.practice_billing_profile import load_billing_profile  # noqa: PLC0415

    note_types = NoteTypeRegistry()
    register_builtin_note_types(note_types)
    session, tokens = _open_tenant_session(engine, tenant_schema)
    try:
        clinician_profiles = PostgresClinicianProfileRepository(session)

        def practitioner(user_id: str) -> Practitioner:
            return practitioner_from(load_billing_profile(session), clinician_profiles.get(user_id))

        service = ExportService(
            PostgresPatientRepository(session),
            PostgresTherapySessionRepository(session),
            PostgresNotesRepository(session),
            practitioner=practitioner,
            note_types=note_types,
        )
        return service.get_patient_export_data(
            patient_id,
            _CLINICIAN,
            export_format,
            **options,
        )
    finally:
        _close_tenant_session(session, tokens)


_PDF_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)


def _pdf_text(pdf: bytes) -> str:
    """The decoded content streams of a reportlab PDF.

    reportlab writes each page's text as ``(...) Tj`` operators inside an
    ASCII85 + Flate encoded stream, so a sentinel with no spaces or markup
    appears verbatim once the streams are decoded. No PDF parser is a
    dependency, and this is all a presence check needs.
    """
    decoded: list[str] = []
    for raw in _PDF_STREAM.findall(pdf):
        data = raw.strip()
        if data.endswith(b"~>"):
            data = base64.a85decode(data, adobe=True)
        with contextlib.suppress(zlib.error):
            data = zlib.decompress(data)
        decoded.append(data.decode("latin-1"))
    return "\n".join(decoded)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestJson:
    def test_everything_is_there_when_both_options_are_on(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        """The control for every absence below: the same rows, all reachable."""
        data = _export(
            engine,
            tenant_schema,
            chart["patient"],
            "json",
            include_transcripts=True,
            include_psychotherapy_notes=True,
        )

        assert data["options"] == {
            "include_transcripts": True,
            "include_psychotherapy_notes": True,
        }
        [session] = data["sessions"]
        assert session["id"] == chart["session"]
        assert _TRANSCRIPT_SENTINEL in session["transcript"]["content"]
        assert _SOAP_SENTINEL in str(session["final_soap_note"])
        standalone = {n["id"]: n for n in data["standalone_notes"]}
        assert set(standalone) == {chart["narrative"], chart["psychotherapy"]}
        assert standalone[chart["psychotherapy"]]["restricted"] is True
        assert _RESTRICTED_SENTINEL in str(standalone[chart["psychotherapy"]]["final_content"])

    def test_default_leaves_out_transcripts_and_psychotherapy_notes(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        data = _export(engine, tenant_schema, chart["patient"], "json")

        assert data["options"] == {
            "include_transcripts": False,
            "include_psychotherapy_notes": False,
        }
        [session] = data["sessions"]
        assert "transcript" not in session
        assert _SOAP_SENTINEL in str(session["final_soap_note"]), "the progress note stays"
        assert [n["id"] for n in data["standalone_notes"]] == [chart["narrative"]]
        assert _NARRATIVE_SENTINEL in str(data["standalone_notes"][0]["final_content"])
        assert _TRANSCRIPT_SENTINEL not in str(data)
        assert _RESTRICTED_SENTINEL not in str(data)


class TestPdf:
    def test_everything_is_there_when_both_options_are_on(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        pdf = _export(
            engine,
            tenant_schema,
            chart["patient"],
            "pdf",
            include_transcripts=True,
            include_psychotherapy_notes=True,
        )["content"]

        assert pdf.startswith(b"%PDF")
        page_text = _pdf_text(pdf)
        # Control: the text layer is readable, so the absences below mean
        # something.
        assert _SOAP_SENTINEL in page_text
        assert "Transcript" in page_text
        assert _TRANSCRIPT_SENTINEL in page_text
        assert "Other notes" in page_text
        assert _NARRATIVE_SENTINEL in page_text
        assert _RESTRICTED_SENTINEL in page_text
        # Standalone notes are headed by the note type's own label.
        assert "Psychotherapy note - " in page_text
        assert "Narrative - " in page_text

    def test_default_leaves_out_transcripts_and_psychotherapy_notes(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        pdf = _export(engine, tenant_schema, chart["patient"], "pdf")["content"]

        assert pdf.startswith(b"%PDF")
        page_text = _pdf_text(pdf)
        assert _SOAP_SENTINEL in page_text
        assert "Other notes" in page_text
        assert _NARRATIVE_SENTINEL in page_text
        assert "Transcript" not in page_text
        assert _TRANSCRIPT_SENTINEL not in page_text
        assert _RESTRICTED_SENTINEL not in page_text


def _unzip(content: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _assert_archive_keeps_its_promises(files: dict[str, bytes]) -> dict[str, Any]:
    """The five files, a document valid against its own schema, true checksums."""
    assert sorted(files) == [
        "README.txt",
        "chart.pdf",
        "manifest.json",
        "patient.json",
        "schema.json",
    ]
    schema = json.loads(files["schema.json"])
    Draft202012Validator.check_schema(schema)
    document = json.loads(files["patient.json"])
    Draft202012Validator(schema).validate(document)

    manifest = json.loads(files["manifest.json"])
    listed = {entry["path"]: entry for entry in manifest["files"]}
    assert set(listed) == set(files) - {"manifest.json"}
    for path, entry in listed.items():
        assert entry["bytes"] == len(files[path]), path
        assert entry["sha256"] == hashlib.sha256(files[path]).hexdigest(), path
    assert manifest["options"] == document["options"]
    assert manifest["exported_at"] == document["exported_at"]
    return document


class TestZip:
    def test_everything_is_there_when_both_options_are_on(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        result = _export(
            engine,
            tenant_schema,
            chart["patient"],
            "zip",
            include_transcripts=True,
            include_psychotherapy_notes=True,
        )

        assert result["content_type"] == "application/zip"
        files = _unzip(result["content"])
        document = _assert_archive_keeps_its_promises(files)
        assert document["schema_version"] == "1.0"
        assert document["options"] == {
            "include_transcripts": True,
            "include_psychotherapy_notes": True,
        }
        assert document["patient"]["identifier"] == chart["patient"]
        assert document["practitioner"] == {
            "name": _PRACTICE_NAME,
            "npi": _NPI,
            "taxonomy_code": _TAXONOMY,
        }
        [encounter] = document["sessions"]
        assert encounter["id"] == chart["session"]
        assert _TRANSCRIPT_SENTINEL in encounter["transcript"]["content"]
        assert encounter["document_reference"]["id"] == chart["soap"]
        assert _SOAP_SENTINEL in str(encounter["document_reference"]["final_content"])
        standalone = {n["id"]: n for n in document["standalone_notes"]}
        assert set(standalone) == {chart["narrative"], chart["psychotherapy"]}
        assert standalone[chart["psychotherapy"]]["restricted"] is True

        page_text = _pdf_text(files["chart.pdf"])
        assert _TRANSCRIPT_SENTINEL in page_text
        assert _RESTRICTED_SENTINEL in page_text

    def test_default_leaves_out_transcripts_and_psychotherapy_notes(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        files = _unzip(_export(engine, tenant_schema, chart["patient"], "zip")["content"])

        document = _assert_archive_keeps_its_promises(files)
        assert document["options"] == {
            "include_transcripts": False,
            "include_psychotherapy_notes": False,
        }
        [encounter] = document["sessions"]
        assert "transcript" not in encounter
        assert _SOAP_SENTINEL in str(encounter["document_reference"]["final_content"])
        assert [n["id"] for n in document["standalone_notes"]] == [chart["narrative"]]
        for name, data in files.items():
            text = data.decode("latin-1") if name != "chart.pdf" else _pdf_text(data)
            assert _TRANSCRIPT_SENTINEL not in text, name
            assert _RESTRICTED_SENTINEL not in text, name
