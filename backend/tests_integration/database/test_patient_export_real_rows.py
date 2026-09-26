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
* a psychotherapy note (``restricted``) by the same clinician;
* one uploaded document in every category, its bytes in a real store;
* one intake form handed in and one never started;
* one appointment, PHQ-9, message thread (opened by the patient, answered by
  the clinician, the message document attached), medication and diagnosis;
* one conversation with the assistant, which is not part of the record.

Each piece of content carries a sentinel string found nowhere else, so an
absence assertion can only pass because the content really was left out. The
all-included case runs against the same rows and is the control: it proves
every sentinel is reachable, in JSON and in the PDF text, before any is
asserted missing.

The archive (``format=zip``) is unpacked and held to its own promises:
``patient.json`` validates against the ``schema.json`` shipped beside it,
and every checksum in ``manifest.json`` matches the file it names. Uploaded
documents are read back through the document service and its store, the
way the route reads them, and each file under ``documents/`` is compared
with the bytes that were stored.

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
import shutil
import tempfile
import uuid
import zipfile
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

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
_MESSAGE_SENTINEL = "PORTALMESSAGESENTINEL4H6J"
_CHAT_SENTINEL = "ASSISTANTCHATSENTINEL8N2P"

_CLINICIAN_NAME = "Dana Reyes"
_CLINICIAN_TIMEZONE = "America/Chicago"

_PRACTICE_NAME = "Harbor Light Counseling"
_NPI = "1234567893"
_TAXONOMY = "101YM0800X"

#: Every category a document can be filed under, and the ones a default copy carries.
_ALL_CATEGORIES = (
    "chart",
    "consent",
    "intake_artifact",
    "message",
    "therapist_private",
    "psychotherapy_notes",
)
_DEFAULT_CATEGORIES = {"chart", "consent", "intake_artifact", "message"}


def _document_bytes(category: str) -> bytes:
    """A small PDF-shaped body whose text names its category and nothing else."""
    return f"%PDF-1.4\n% DOCUMENTSENTINEL-{category.upper()}\n%%EOF\n".encode()


def _storage_root(tenant_schema: str) -> Path:
    """Where this module's store keeps its files: one directory per schema."""
    return Path(tempfile.gettempdir()) / f"{tenant_schema}-documents"


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
    shutil.rmtree(_storage_root(schema), ignore_errors=True)
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
        ids.update(_seed_documents(session, tenant_schema, patient_id, now))
        session.commit()
    finally:
        _close_tenant_session(session, tokens)
    ids.update(_seed_intake(engine, tenant_schema, patient_id, now))
    ids.update(_seed_clinical(engine, tenant_schema, patient_id, ids, now))
    return ids


def _seed_clinical(
    engine: Engine, tenant_schema: str, patient_id: str, ids: dict[str, str], now: datetime
) -> dict[str, str]:
    """One row of each clinical list, through the repositories that own them.

    The thread is opened on a patient-armed session, as the portal opens one,
    and answered on the clinician's. The assistant conversation is the
    clinician's own, about this patient.
    """
    from app.models import (  # noqa: PLC0415
        ChatConversation,
        ChatMessage,
        PatientMessage,
        PatientMessageThread,
        User,
        UserPreferences,
    )
    from app.repositories.postgres.appointment import (  # noqa: PLC0415
        PostgresAppointmentRepository,
    )
    from app.repositories.postgres.chat import PostgresChatRepository  # noqa: PLC0415
    from app.repositories.postgres.diagnostic_assessment import (  # noqa: PLC0415
        PostgresDiagnosticAssessmentRepository,
    )
    from app.repositories.postgres.medication import (  # noqa: PLC0415
        PostgresMedicationRepository,
    )
    from app.repositories.postgres.outcome_measure import (  # noqa: PLC0415
        PostgresOutcomeMeasureRepository,
    )
    from app.repositories.postgres.patient_message import (  # noqa: PLC0415
        PostgresPatientMessageRepository,
    )
    from app.repositories.postgres.user import PostgresUserRepository  # noqa: PLC0415
    from app.scheduling_engine.models.appointment import Appointment  # noqa: PLC0415

    seeded = {
        "appointment": str(uuid.uuid4()),
        "measure": str(uuid.uuid4()),
        "thread": str(uuid.uuid4()),
        "message:patient": str(uuid.uuid4()),
        "message:clinician": str(uuid.uuid4()),
        "medication": str(uuid.uuid4()),
        "diagnosis": str(uuid.uuid4()),
        "chat": str(uuid.uuid4()),
    }

    session, tokens = _open_patient_session(engine, tenant_schema, patient_id)
    try:
        messages = PostgresPatientMessageRepository(session)
        messages.add_patient_thread(
            PatientMessageThread(
                id=seeded["thread"],
                patient_id=patient_id,
                subject="Moving Thursday",
                status="open",
                created_at=now,
                last_message_at=now,
            ),
            PatientMessage(
                id=seeded["message:patient"],
                thread_id=seeded["thread"],
                patient_id=patient_id,
                sender="patient",
                body=f"Could we move Thursday? {_MESSAGE_SENTINEL}",
                created_at=now,
            ),
        )
        messages.link_attachments(
            message_id=seeded["message:patient"],
            patient_id=patient_id,
            document_ids=[ids["document:message"]],
            created_at=now,
        )
        session.commit()
    finally:
        _close_patient_session(session, tokens)

    session, tokens = _open_tenant_session(engine, tenant_schema)
    try:
        PostgresPatientMessageRepository(session).add_reply(
            PatientMessage(
                id=seeded["message:clinician"],
                thread_id=seeded["thread"],
                patient_id=patient_id,
                sender="clinician",
                body="Friday at the same time works.",
                created_at=now,
            ),
            _CLINICIAN,
        )
        users = PostgresUserRepository(session)
        users.update(
            User(
                id=_CLINICIAN,
                email=f"{tenant_schema}@example.test",
                name=_CLINICIAN_NAME,
                created_at=now,
            )
        )
        users.save_preferences(_CLINICIAN, UserPreferences(timezone=_CLINICIAN_TIMEZONE))
        PostgresAppointmentRepository(session).create(
            Appointment(
                id=seeded["appointment"],
                user_id=_CLINICIAN,
                patient_id=patient_id,
                title="Session",
                start_at=now,
                end_at=now + timedelta(minutes=50),
                duration_minutes=50,
                status="completed",
                session_type="Individual therapy",
                provider="zoom",
                video_link="https://meet.example/room",
                place_of_service="10",
                session_id=ids["session"],
                created_at=now,
                updated_at=now,
            )
        )
        PostgresOutcomeMeasureRepository(session).add(
            {
                "id": seeded["measure"],
                "patient_id": patient_id,
                "session_id": ids["session"],
                "instrument": "phq9",
                "total_score": 9,
                "item_scores": {str(i): 1 for i in range(1, 10)},
                "is_complete": True,
                "source": "patient_self_report",
                "administered_at": now,
                "created_by": _CLINICIAN,
                "created_at": now,
                "updated_at": now,
            },
            _CLINICIAN,
        )
        PostgresMedicationRepository(session).create(
            {
                "id": seeded["medication"],
                "patient_id": patient_id,
                "drug_name": "Sertraline",
                "dose": "50 mg daily",
                "status": "active",
                "started_at": now.date(),
                "created_by": _CLINICIAN,
                "created_at": now,
                "updated_at": now,
            },
            _CLINICIAN,
        )
        PostgresDiagnosticAssessmentRepository(session).add(
            {
                "id": seeded["diagnosis"],
                "patient_id": patient_id,
                "instrument": "gad",
                "definition_version": 1,
                "criterion_responses": {},
                "gate_responses": {},
                "meets_criteria": True,
                "determined_icd10": "F41.1",
                "diagnosis_label": "Generalized anxiety disorder",
                "source": "manual",
                "assessed_at": now,
                "created_by": _CLINICIAN,
                "created_at": now,
                "updated_at": now,
            },
            _CLINICIAN,
        )
        chat = PostgresChatRepository(session)
        chat.add_conversation(
            ChatConversation(
                id=seeded["chat"],
                patient_id=patient_id,
                owner_user_id=_CLINICIAN,
                title="Assistant",
                caller_system_prompt="You help a clinician.",
                caller_feature_key="chart_chat",
                created_at=now,
            ),
            _CLINICIAN,
        )
        chat.add_message(
            ChatMessage(
                id=str(uuid.uuid4()),
                conversation_id=seeded["chat"],
                sequence=1,
                role="user",
                content=f"Summarise the last session. {_CHAT_SENTINEL}",
                created_at=now,
            )
        )
        session.commit()
    finally:
        _close_tenant_session(session, tokens)
    return seeded


def _seed_documents(
    session: Session, tenant_schema: str, patient_id: str, now: datetime
) -> dict[str, str]:
    """One finalized upload per category, its bytes written to the store."""
    from app.models import DocumentCategory, PatientDocument  # noqa: PLC0415
    from app.repositories.postgres.patient_document import (  # noqa: PLC0415
        PostgresPatientDocumentRepository,
    )
    from app.services.file_storage import LocalFileStorage  # noqa: PLC0415

    repo = PostgresPatientDocumentRepository(session)
    storage = LocalFileStorage()
    ids: dict[str, str] = {}
    for category in _ALL_CATEGORIES:
        document_id = str(uuid.uuid4())
        object_name = f"{tenant_schema}/{category}/{document_id}"
        data = _document_bytes(category)
        storage.upload_bytes(
            bucket=str(_storage_root(tenant_schema)),
            object_name=object_name,
            data=data,
            content_type="application/pdf",
        )
        repo.add(
            PatientDocument(
                id=document_id,
                patient_id=patient_id,
                user_id=_CLINICIAN,
                filename=f"{category}-upload.pdf",
                mime_type="application/pdf",
                gcs_path=object_name,
                size_bytes=len(data),
                created_at=now,
                finalized_at=now,
                category=DocumentCategory(category),
            )
        )
        ids[f"document:{category}"] = document_id
    return ids


def _seed_intake(
    engine: Engine, tenant_schema: str, patient_id: str, now: datetime
) -> dict[str, str]:
    """Two published one-question forms: one handed in, one sent and not started."""
    submitted, unstarted = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
        conn.execute(text("SELECT set_config('app.current_user_id', :u, false)"), {"u": _CLINICIAN})
        for assignment_id, status, submitted_at in (
            (submitted, "submitted", now),
            (unstarted, "assigned", None),
        ):
            template_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO intake_packet_templates (id, name, created_by, created_at) "
                    "VALUES (CAST(:tid AS uuid), 'Intake', CAST(:u AS uuid), :now)"
                ),
                {"tid": template_id, "u": _CLINICIAN, "now": now},
            )
            conn.execute(
                text(
                    "INSERT INTO intake_packet_versions "
                    "(id, template_id, version, published_at, published_by, created_at) "
                    "VALUES (CAST(:vid AS uuid), CAST(:tid AS uuid), 1, :now, "
                    "CAST(:u AS uuid), :now)"
                ),
                {"vid": version_id, "tid": template_id, "u": _CLINICIAN, "now": now},
            )
            conn.execute(
                text(
                    "INSERT INTO intake_item_definitions "
                    "(id, version_id, key, position, item_type, required, label, config) "
                    "VALUES (CAST(:iid AS uuid), CAST(:vid AS uuid), 'reason', 1, 'reason', "
                    "true, NULL, '{}'::jsonb)"
                ),
                {"iid": str(uuid.uuid4()), "vid": version_id},
            )
            conn.execute(
                text(
                    "INSERT INTO patient_intake_assignments "
                    "(id, patient_id, version_id, status, assigned_by, assigned_at, "
                    "submitted_at, updated_at) "
                    "VALUES (CAST(:id AS uuid), CAST(:pid AS uuid), CAST(:vid AS uuid), "
                    ":status, CAST(:u AS uuid), :now, :submitted_at, :now)"
                ),
                {
                    "id": assignment_id,
                    "pid": patient_id,
                    "vid": version_id,
                    "status": status,
                    "u": _CLINICIAN,
                    "now": now,
                    "submitted_at": submitted_at,
                },
            )
    return {"intake:submitted": submitted, "intake:unstarted": unstarted}


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


def _open_patient_session(
    engine: Engine, tenant_schema: str, patient_id: str
) -> tuple[Session, tuple[Any, Any]]:
    """An ORM session on the practice schema, armed as the patient and nobody else."""
    from app.db import (  # noqa: PLC0415
        _current_patient_id,
        _current_tenant_schema,
        arm_current_patient_id,
    )
    from sqlalchemy.orm import Session as OrmSession  # noqa: PLC0415

    tokens = (_current_tenant_schema.set(tenant_schema), _current_patient_id.set(patient_id))
    session = OrmSession(bind=engine)
    session.execute(text(f"SET search_path = {tenant_schema}, platform, public"))
    arm_current_patient_id(session, patient_id)
    return session, tokens


def _close_patient_session(session: Session, tokens: tuple[Any, Any]) -> None:
    from app.db import _current_patient_id, _current_tenant_schema  # noqa: PLC0415

    session.close()
    _current_tenant_schema.reset(tokens[0])
    _current_patient_id.reset(tokens[1])


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
    tenant session, notes are labelled by the built-in note types, uploaded
    files come through the document service and its store, each
    submitted form is rendered by the intake export's own renderer, and the
    clinical lists come through their own repositories.
    """
    from app.notes import NoteTypeRegistry, register_builtin_note_types  # noqa: PLC0415
    from app.repositories.postgres.appointment import (  # noqa: PLC0415
        PostgresAppointmentRepository,
    )
    from app.repositories.postgres.clinician_profile import (  # noqa: PLC0415
        PostgresClinicianProfileRepository,
    )
    from app.repositories.postgres.diagnostic_assessment import (  # noqa: PLC0415
        PostgresDiagnosticAssessmentRepository,
    )
    from app.repositories.postgres.intake_document import (  # noqa: PLC0415
        PostgresIntakeDocumentRepository,
    )
    from app.repositories.postgres.intake_packet import (  # noqa: PLC0415
        PostgresIntakePacketRepository,
    )
    from app.repositories.postgres.medication import (  # noqa: PLC0415
        PostgresMedicationRepository,
    )
    from app.repositories.postgres.note import PostgresNotesRepository  # noqa: PLC0415
    from app.repositories.postgres.outcome_measure import (  # noqa: PLC0415
        PostgresOutcomeMeasureRepository,
    )
    from app.repositories.postgres.patient import PostgresPatientRepository  # noqa: PLC0415
    from app.repositories.postgres.patient_document import (  # noqa: PLC0415
        PostgresPatientDocumentRepository,
    )
    from app.repositories.postgres.patient_intake_artifact import (  # noqa: PLC0415
        PostgresPatientIntakeArtifactRepository,
    )
    from app.repositories.postgres.patient_intake_assignment import (  # noqa: PLC0415
        PostgresPatientIntakeAssignmentRepository,
    )
    from app.repositories.postgres.patient_intake_signature import (  # noqa: PLC0415
        PostgresPatientIntakeSignatureRepository,
    )
    from app.repositories.postgres.patient_message import (  # noqa: PLC0415
        PostgresPatientMessageRepository,
    )
    from app.repositories.postgres.session import (  # noqa: PLC0415
        PostgresTherapySessionRepository,
    )
    from app.repositories.postgres.user import PostgresUserRepository  # noqa: PLC0415
    from app.routes.patient_intake_export import _FormRenderer  # noqa: PLC0415
    from app.services import ExportService  # noqa: PLC0415
    from app.services.export_archive import practitioner_from  # noqa: PLC0415
    from app.services.export_clinical import ClinicalRecordSource  # noqa: PLC0415
    from app.services.file_storage import LocalFileStorage  # noqa: PLC0415
    from app.services.patient_documents_service import PatientDocumentsService  # noqa: PLC0415
    from app.services.patient_intake_assignment_service import (  # noqa: PLC0415
        IntakeAssignmentService,
    )
    from app.services.patient_intake_export_service import IntakeExportService  # noqa: PLC0415
    from app.services.patient_intake_review_service import IntakeReviewService  # noqa: PLC0415
    from app.services.practice_billing_profile import load_billing_profile  # noqa: PLC0415

    note_types = NoteTypeRegistry()
    register_builtin_note_types(note_types)
    session, tokens = _open_tenant_session(engine, tenant_schema)
    try:
        clinician_profiles = PostgresClinicianProfileRepository(session)

        def practitioner(user_id: str) -> Practitioner:
            return practitioner_from(load_billing_profile(session), clinician_profiles.get(user_id))

        documents = PatientDocumentsService(
            repo=PostgresPatientDocumentRepository(session),
            settings=Mock(patient_documents_gcs_bucket=str(_storage_root(tenant_schema))),
            storage=LocalFileStorage(),
        )
        assignment_repo = PostgresPatientIntakeAssignmentRepository(session)
        packets = PostgresIntakePacketRepository(session)
        assignments = IntakeAssignmentService(assignment_repo, packets)
        forms = _FormRenderer(
            assignments,
            IntakeExportService(
                assignments,
                IntakeReviewService(assignment_repo, packets),
                PostgresPatientIntakeSignatureRepository(session),
                PostgresIntakeDocumentRepository(session),
                PostgresPatientIntakeArtifactRepository(session),
                PostgresPatientDocumentRepository(session),
            ),
            _PRACTICE_NAME,
            UTC,
            "https://pablo.example",
        )
        clinical = ClinicalRecordSource(
            appointments=PostgresAppointmentRepository(session),
            users=PostgresUserRepository(session),
            outcome_measures=PostgresOutcomeMeasureRepository(session),
            messages=PostgresPatientMessageRepository(session),
            medications=PostgresMedicationRepository(session),
            diagnoses=PostgresDiagnosticAssessmentRepository(session),
        )
        service = ExportService(
            PostgresPatientRepository(session),
            PostgresTherapySessionRepository(session),
            PostgresNotesRepository(session),
            practitioner=practitioner,
            note_types=note_types,
            documents=documents,
            intake_forms=forms.submitted_forms,
            clinical_record=clinical.read,
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
    """The five files and the carried ones, a document valid against its own
    schema, true checksums."""
    described = {"README.txt", "chart.pdf", "manifest.json", "patient.json", "schema.json"}
    assert described <= set(files)
    carried = set(files) - described
    assert all(name.startswith(("documents/", "intake/")) for name in carried), carried
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
        assert document["schema_version"] == "1.2"
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


def _carried_documents(files: dict[str, bytes], document: dict[str, Any]) -> dict[str, bytes]:
    """Each file under ``documents/`` by category, held to its ``patient.json`` entry."""
    entries = document["documents"]
    assert {e["archive_path"] for e in entries} == {
        name for name in files if name.startswith("documents/")
    }
    by_category: dict[str, bytes] = {}
    for entry in entries:
        data = files[entry["archive_path"]]
        assert entry["bytes"] == len(data), entry["archive_path"]
        assert entry["sha256"] == hashlib.sha256(data).hexdigest(), entry["archive_path"]
        by_category[entry["category"]] = data
    return by_category


class TestZipDocuments:
    def test_psychotherapy_notes_join_with_the_option_and_therapist_private_never_does(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        """The control for the default below: every admitted category is reachable."""
        files = _unzip(
            _export(
                engine,
                tenant_schema,
                chart["patient"],
                "zip",
                include_transcripts=True,
                include_psychotherapy_notes=True,
            )["content"]
        )
        document = _assert_archive_keeps_its_promises(files)

        carried = _carried_documents(files, document)
        assert set(carried) == {*_DEFAULT_CATEGORIES, "psychotherapy_notes"}
        for category, data in carried.items():
            assert data == _document_bytes(category), "the stored bytes, unchanged"
        by_id = {e["id"]: e for e in document["documents"]}
        assert chart["document:therapist_private"] not in by_id
        assert by_id[chart["document:chart"]] | {"uploaded_at": None} == {
            "id": chart["document:chart"],
            "category": "chart",
            "filename": "chart-upload.pdf",
            "content_type": "application/pdf",
            "bytes": len(_document_bytes("chart")),
            "sha256": hashlib.sha256(_document_bytes("chart")).hexdigest(),
            "uploaded_at": None,
            "uploaded_by": "clinician",
            "archive_path": f"documents/{chart['document:chart']}__chart-upload.pdf",
        }
        private = _document_bytes("therapist_private")
        assert all(private not in data for data in files.values())

    def test_default_carries_the_record_categories_and_neither_restricted_one(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        files = _unzip(_export(engine, tenant_schema, chart["patient"], "zip")["content"])
        document = _assert_archive_keeps_its_promises(files)

        carried = _carried_documents(files, document)
        assert set(carried) == _DEFAULT_CATEGORIES
        for category, data in carried.items():
            assert data == _document_bytes(category)
        for restricted in ("therapist_private", "psychotherapy_notes"):
            stored = _document_bytes(restricted)
            assert all(stored not in data for data in files.values()), restricted
            assert chart[f"document:{restricted}"] not in files["patient.json"].decode()

    def test_chart_pdf_lists_the_documents_beside_it(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        files = _unzip(_export(engine, tenant_schema, chart["patient"], "zip")["content"])
        document = json.loads(files["patient.json"])

        page_text = _pdf_text(files["chart.pdf"])
        # A PDF string escapes its parentheses.
        assert f"Documents \\({len(_DEFAULT_CATEGORIES)}\\)" in page_text
        for entry in document["documents"]:
            assert entry["filename"] in page_text
            assert entry["sha256"] in page_text
        assert "therapist_private-upload.pdf" not in page_text

    def test_each_submitted_intake_form_is_carried_as_its_own_export(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        files = _unzip(_export(engine, tenant_schema, chart["patient"], "zip")["content"])
        _assert_archive_keeps_its_promises(files)

        intake = {name for name in files if name.startswith("intake/")}
        assert intake == {f"intake/{chart['intake:submitted']}.html"}
        form = files[f"intake/{chart['intake:submitted']}.html"].decode()
        assert form.startswith("<!DOCTYPE html>")
        assert _PRACTICE_NAME in form
        manifest = json.loads(files["manifest.json"])
        kinds = {entry["path"]: entry["kind"] for entry in manifest["files"]}
        assert kinds[f"intake/{chart['intake:submitted']}.html"] == "intake_form"
        assert {kinds[name] for name in files if name.startswith("documents/")} == {"document"}


class TestZipClinical:
    def test_each_clinical_list_is_in_patient_json_as_the_rows_say(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        result = _export(engine, tenant_schema, chart["patient"], "zip")
        files = _unzip(result["content"])
        document = _assert_archive_keeps_its_promises(files)

        [appointment] = document["appointments"]
        assert appointment | {"start": None, "end": None} == {
            "id": chart["appointment"],
            "start": None,
            "end": None,
            "timezone": _CLINICIAN_TIMEZONE,
            "appointment_type": "Individual therapy",
            "status": "completed",
            "clinician_name": _CLINICIAN_NAME,
            "telehealth": True,
            "place_of_service": "10",
            "note_type": "soap",
            "session_id": chart["session"],
        }

        [measure] = document["outcome_measures"]
        assert measure | {"administered_at": None} == {
            "id": chart["measure"],
            "instrument": "phq9",
            "instrument_name": "PHQ-9",
            "administered_at": None,
            "total_score": 9,
            "severity": "mild",
            "item_responses": {str(i): 1 for i in range(1, 10)},
            "is_complete": True,
            "source": "patient_self_report",
            "session_id": chart["session"],
        }

        [thread] = document["message_threads"]
        assert thread["id"] == chart["thread"]
        assert thread["subject"] == "Moving Thursday"
        assert [(m["id"], m["sender"]) for m in thread["messages"]] == [
            (chart["message:patient"], "patient"),
            (chart["message:clinician"], "clinician"),
        ]
        opening = thread["messages"][0]
        assert _MESSAGE_SENTINEL in opening["body"]
        assert opening["attachment_document_ids"] == [chart["document:message"]]
        assert chart["document:message"] in {d["id"] for d in document["documents"]}, (
            "an attachment names a file the archive carries"
        )
        assert result["message_threads"] == [(chart["thread"], 2)]

        [medication] = document["medications"]
        assert medication | {"started_on": None} == {
            "id": chart["medication"],
            "drug_name": "Sertraline",
            "dose": "50 mg daily",
            "status": "active",
            "started_on": None,
            "stopped_on": None,
            "stop_reason": None,
            "notes": None,
        }

        [diagnosis] = document["diagnoses"]
        assert diagnosis | {"assessed_at": None} == {
            "id": chart["diagnosis"],
            "icd10_code": "F41.1",
            "description": "Generalized anxiety disorder",
            "assessed_at": None,
            "status": "confirmed",
            "instrument": "gad",
            "meets_criteria": True,
            "session_id": None,
        }

    def test_assistant_conversations_are_not_in_the_copy(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        for options in ({}, {"include_transcripts": True, "include_psychotherapy_notes": True}):
            files = _unzip(
                _export(engine, tenant_schema, chart["patient"], "zip", **options)["content"]
            )
            _assert_archive_keeps_its_promises(files)
            for name, data in files.items():
                text = _pdf_text(data) if name == "chart.pdf" else data.decode("latin-1")
                assert _CHAT_SENTINEL not in text, name
                assert chart["chat"] not in text, name
            # Control: the portal thread beside it is in the same copy.
            assert _MESSAGE_SENTINEL in files["patient.json"].decode()

    def test_chart_pdf_has_a_section_per_list(
        self, engine: Engine, tenant_schema: str, chart: dict[str, str]
    ) -> None:
        for export_format in ("zip", "pdf"):
            result = _export(engine, tenant_schema, chart["patient"], export_format)
            pdf = (
                result["content"]
                if export_format == "pdf"
                else _unzip(result["content"])["chart.pdf"]
            )

            page_text = _pdf_text(pdf)
            # A PDF string escapes its parentheses.
            headings = [
                "Appointments \\(1\\)",
                "Outcome measures \\(1\\)",
                "Messages \\(1\\)",
                "Medications \\(1\\)",
                "Diagnoses \\(1\\)",
            ]
            positions = [page_text.find(heading) for heading in headings]
            assert -1 not in positions, (export_format, positions)
            assert positions == sorted(positions), "in the order the lists are named"
            assert _MESSAGE_SENTINEL in page_text
            assert "PHQ-9" in page_text
            assert "Sertraline" in page_text
            assert "F41.1" in page_text
            assert _CHAT_SENTINEL not in page_text
