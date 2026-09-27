# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinical record in an export: each list read as the exporting
clinician, live rows only, and laid out in ``patient.json`` and ``chart.pdf``.

The Postgres harness test builds the same lists from real rows; this one
pins the mapping and the access rule over the in-memory repositories.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest
from app.medications.repository import InMemoryMedicationRepository
from app.models import (
    Patient,
    PatientMessage,
    PatientMessageThread,
    User,
    UserPreferences,
)
from app.repositories.diagnostic_assessment import InMemoryDiagnosticAssessmentRepository
from app.repositories.outcome_measure import InMemoryOutcomeMeasureRepository
from app.repositories.patient_message import InMemoryPatientMessageRepository
from app.repositories.user import InMemoryUserRepository
from app.scheduling_engine.models.appointment import Appointment
from app.scheduling_engine.repositories.appointment import InMemoryAppointmentRepository
from app.services import ExportService
from app.services.export_clinical import ClinicalRecordSource
from jsonschema import Draft202012Validator

_PATIENT = "patient-1"
_CLINICIAN = "clinician-1"
_T0 = datetime(2026, 3, 2, 15, 0, tzinfo=UTC)


def _at(day: int, hour: int = 15) -> datetime:
    return _T0.replace(day=day, hour=hour)


@pytest.fixture
def source() -> ClinicalRecordSource:
    appointments = InMemoryAppointmentRepository()
    appointments.grant_access(_PATIENT, _CLINICIAN)
    for appointment_id, day, status, provider in (
        ("appt-later", 9, "confirmed", "zoom"),
        ("appt-first", 2, "completed", None),
    ):
        appointments.create(
            Appointment(
                id=appointment_id,
                user_id=_CLINICIAN,
                patient_id=_PATIENT,
                title="Session",
                start_at=_at(day),
                end_at=_at(day, 16),
                duration_minutes=50,
                status=status,
                session_type="Individual therapy",
                provider=provider,
                place_of_service=None if provider else "11",
                session_id="session-1" if status == "completed" else None,
            )
        )

    users = InMemoryUserRepository()
    users.update(User(id=_CLINICIAN, email="c@example.test", name="Dana Reyes", created_at=_T0))
    users.save_preferences(_CLINICIAN, UserPreferences(timezone="America/Chicago"))

    measures = InMemoryOutcomeMeasureRepository()
    measures.grant_access(_PATIENT, _CLINICIAN)
    for measure_id, deleted in (("phq9-live", None), ("phq9-deleted", _T0)):
        measures.add(
            {
                "id": measure_id,
                "patient_id": _PATIENT,
                "session_id": None,
                "appointment_id": None,
                "instrument": "phq9",
                "total_score": 9,
                "item_scores": {str(i): 1 for i in range(1, 10)},
                "is_complete": True,
                "source": "patient_self_report",
                "item_citations": None,
                "administered_at": _at(3),
                "created_by": _CLINICIAN,
                "created_at": _at(3),
                "updated_at": _at(3),
                "deleted_at": deleted,
            },
            _CLINICIAN,
        )

    messages = InMemoryPatientMessageRepository()
    messages.grant_access(_PATIENT, _CLINICIAN)
    thread = PatientMessageThread(
        id="thread-1",
        patient_id=_PATIENT,
        subject="Moving Thursday",
        status="open",
        created_at=_at(4),
        last_message_at=_at(4),
    )
    opening = PatientMessage(
        id="message-1",
        thread_id=thread.id,
        patient_id=_PATIENT,
        sender="patient",
        body="Can we move <Thursday> & keep the time?",
        created_at=_at(4),
    )
    messages.add_patient_thread(thread, opening)
    messages.describe_document(
        "doc-1", filename="card.pdf", mime_type="application/pdf", size_bytes=3
    )
    messages.link_attachments(
        message_id=opening.id, patient_id=_PATIENT, document_ids=["doc-1"], created_at=_at(4)
    )
    messages.add_reply(
        PatientMessage(
            id="message-2",
            thread_id=thread.id,
            patient_id=_PATIENT,
            sender="clinician",
            body="Yes, Friday works.",
            created_at=_at(4, 17),
        ),
        _CLINICIAN,
    )

    medications = InMemoryMedicationRepository()
    medications.grant_access(_PATIENT, _CLINICIAN)
    medications.create(
        {
            "id": "med-1",
            "patient_id": _PATIENT,
            "drug_name": "Sertraline",
            "dose": "50 mg daily",
            "status": "active",
            "started_at": date(2026, 1, 5),
            "stopped_at": None,
            "stop_reason": None,
            "notes": None,
            "created_by": _CLINICIAN,
            "created_at": _T0,
            "updated_at": _T0,
            "deleted_at": None,
        },
        _CLINICIAN,
    )

    diagnoses = InMemoryDiagnosticAssessmentRepository()
    diagnoses.grant_access(_PATIENT, _CLINICIAN)
    for assessment_id, code, label, day in (
        ("dx-confirmed", "F41.1", "Generalized anxiety disorder", 6),
        ("dx-open", None, None, 5),
    ):
        diagnoses.add(
            {
                "id": assessment_id,
                "patient_id": _PATIENT,
                "session_id": None,
                "appointment_id": None,
                "instrument": "gad",
                "definition_version": 1,
                "criterion_responses": {},
                "gate_responses": {},
                "meets_criteria": True if code else None,
                "determined_icd10": code,
                "diagnosis_label": label,
                "source": "manual",
                "assessed_at": _at(day),
                "created_by": _CLINICIAN,
                "created_at": _at(day),
                "updated_at": _at(day),
                "deleted_at": None,
            },
            _CLINICIAN,
        )

    return ClinicalRecordSource(
        appointments=appointments,
        users=users,
        outcome_measures=measures,
        messages=messages,
        medications=medications,
        diagnoses=diagnoses,
    )


def test_each_list_is_read_and_mapped(source: ClinicalRecordSource) -> None:
    record = source.read(_PATIENT, _CLINICIAN)

    assert [a.id for a in record.appointments] == ["appt-first", "appt-later"], "oldest first"
    first, later = record.appointments
    assert first.model_dump() == {
        "id": "appt-first",
        "start": _at(2),
        "end": _at(2, 16),
        "timezone": "America/Chicago",
        "appointment_type": "Individual therapy",
        "status": "completed",
        "clinician_name": "Dana Reyes",
        "telehealth": False,
        "place_of_service": "11",
        "note_type": "soap",
        "service_code": None,
        "session_id": "session-1",
    }
    assert later.telehealth is True

    [measure] = record.outcome_measures
    assert measure.id == "phq9-live", "a deleted measure is not on the chart"
    assert measure.instrument_name == "PHQ-9"
    assert measure.severity == "mild"
    assert measure.item_responses == {str(i): 1 for i in range(1, 10)}

    [thread] = record.message_threads
    assert [(m.sender, m.attachment_document_ids) for m in thread.messages] == [
        ("patient", ["doc-1"]),
        ("clinician", []),
    ]

    [medication] = record.medications
    assert medication.started_on == date(2026, 1, 5)

    assert [(d.id, d.status, d.icd10_code) for d in record.diagnoses] == [
        ("dx-open", "unconfirmed", None),
        ("dx-confirmed", "confirmed", "F41.1"),
    ]


def test_a_clinician_without_access_gets_an_empty_record(source: ClinicalRecordSource) -> None:
    record = source.read(_PATIENT, "clinician-without-a-grant")

    assert record.appointments == []
    assert record.outcome_measures == []
    assert record.message_threads == []
    assert record.medications == []
    assert record.diagnoses == []


def test_the_archive_carries_each_list_and_the_pdf_heads_a_section_for_it(
    source: ClinicalRecordSource,
) -> None:
    patients, sessions, notes = Mock(), Mock(), Mock()
    patients.get.return_value = Patient(
        id=_PATIENT, first_name="Robin", last_name="Ash", created_at=_T0, updated_at=_T0
    )
    sessions.list_by_patient.return_value = []
    notes.list_by_patient.return_value = []
    service = ExportService(patients, sessions, notes, clinical_record=source.read)

    result = service.get_patient_export_data(_PATIENT, _CLINICIAN, "zip")

    with zipfile.ZipFile(io.BytesIO(result["content"])) as archive:
        document = json.loads(archive.read("patient.json"))
        schema = json.loads(archive.read("schema.json"))
    Draft202012Validator(schema).validate(document)
    assert document["schema_version"] == "1.4"
    assert {key: len(document[key]) for key in _LISTS} == {
        "appointments": 2,
        "outcome_measures": 1,
        "message_threads": 1,
        "medications": 1,
        "diagnoses": 2,
    }
    assert document["medications"][0]["started_on"] == "2026-01-05"
    assert result["message_threads"] == [("thread-1", 2)]

    pdf = service.get_patient_export_data(_PATIENT, _CLINICIAN, "pdf")
    assert pdf["content"].startswith(b"%PDF")
    assert pdf["message_threads"] == [("thread-1", 2)]


_LISTS = ("appointments", "outcome_measures", "message_threads", "medications", "diagnoses")
