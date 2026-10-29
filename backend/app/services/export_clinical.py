# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The clinical record beyond notes, as the export carries it.

The schedule, scored instruments, secure messages, the medication list and
diagnostic assessments. Each is read through the repository its own chart
tab uses, as the exporting clinician, so a row that clinician cannot open
is not in the copy either. Soft-deleted rows are left out, as they are on
the chart.

None of it is restricted, so the record-set selector is not consulted.
Conversations with the assistant are not part of the record and are never
read here. Treatment and safety plans are notes, and arrive with the notes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from ..models import UserPreferences
from ..models.export import (
    Communication,
    Condition,
    ExportAppointment,
    ExportMessageThread,
    MedicationStatement,
    MessageSender,
    Observation,
    OutcomeMeasureSource,
)
from ..models.scheduling import is_telehealth
from ..outcome_measures.instruments import get_instrument
from ..outcome_measures.service import OutcomeMeasureService

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import date, datetime

    from ..medications.repository import MedicationRepository
    from ..models import PatientMessageThread
    from ..repositories.diagnostic_assessment import DiagnosticAssessmentRepository
    from ..repositories.outcome_measure import OutcomeMeasureRepository
    from ..repositories.patient_message import PatientMessageRepository
    from ..repositories.user import UserRepository
    from ..scheduling_engine.models.appointment import Appointment
    from ..scheduling_engine.repositories.appointment import AppointmentRepository


@dataclass(frozen=True)
class ClinicalRecord:
    """Each list as it goes into ``patient.json`` and ``chart.pdf``."""

    appointments: list[ExportAppointment] = field(default_factory=list)
    outcome_measures: list[Observation] = field(default_factory=list)
    message_threads: list[ExportMessageThread] = field(default_factory=list)
    medications: list[MedicationStatement] = field(default_factory=list)
    diagnoses: list[Condition] = field(default_factory=list)


#: Given the patient's id and the exporting clinician's, their clinical record.
ClinicalRecordReader = Callable[[str, str], ClinicalRecord]


class ClinicalRecordSource:
    """Reads one patient's clinical record through the chart's repositories."""

    def __init__(
        self,
        *,
        appointments: AppointmentRepository,
        users: UserRepository,
        outcome_measures: OutcomeMeasureRepository,
        messages: PatientMessageRepository,
        medications: MedicationRepository,
        diagnoses: DiagnosticAssessmentRepository,
    ) -> None:
        self._appointments = appointments
        self._users = users
        self._outcome_measures = OutcomeMeasureService(outcome_measures)
        self._messages = messages
        self._medications = medications
        self._diagnoses = diagnoses

    def read(self, patient_id: str, user_id: str) -> ClinicalRecord:
        return ClinicalRecord(
            appointments=self._read_appointments(patient_id, user_id),
            outcome_measures=self._read_outcome_measures(patient_id, user_id),
            message_threads=self._read_message_threads(patient_id, user_id),
            medications=[
                _medication(row) for row in self._medications.list_by_patient(patient_id, user_id)
            ],
            diagnoses=self._read_diagnoses(patient_id, user_id),
        )

    def _read_appointments(self, patient_id: str, user_id: str) -> list[ExportAppointment]:
        """Every appointment, cancelled ones included: the schedule is the record."""
        appointments = self._appointments.list_by_patient(user_id, patient_id)
        owners = sorted({a.user_id for a in appointments})
        preferences = self._users.get_preferences_many(owners) if owners else {}
        names: dict[str, str | None] = {}
        for owner in owners:
            user = self._users.get(owner)
            names[owner] = user.name if user is not None and user.name else None
        return [
            _appointment(a, names.get(a.user_id), _timezone(preferences, a.user_id))
            for a in sorted(appointments, key=lambda a: a.start_at)
        ]

    def _read_outcome_measures(self, patient_id: str, user_id: str) -> list[Observation]:
        out: list[Observation] = []
        for measure in self._outcome_measures.list_for_patient(patient_id, user_id):
            definition = get_instrument(measure.instrument)
            out.append(
                Observation(
                    id=measure.id,
                    instrument=measure.instrument,
                    instrument_name=definition.display_name if definition else None,
                    administered_at=measure.administered_at,
                    total_score=measure.total_score,
                    severity=measure.severity,
                    item_responses=measure.item_scores,
                    is_complete=measure.is_complete,
                    source=cast("OutcomeMeasureSource", measure.source),
                    session_id=measure.session_id,
                )
            )
        return out

    def _read_message_threads(self, patient_id: str, user_id: str) -> list[ExportMessageThread]:
        threads = [t for t, _unread in self._messages.list_threads_for_patient(patient_id, user_id)]
        return [
            self._thread(thread, user_id) for thread in sorted(threads, key=lambda t: t.created_at)
        ]

    def _thread(self, thread: PatientMessageThread, user_id: str) -> ExportMessageThread:
        messages = self._messages.list_messages(thread.id, user_id)
        attachments = self._messages.list_attachments([m.id for m in messages], thread.patient_id)
        return ExportMessageThread(
            id=thread.id,
            subject=thread.subject,
            status="closed" if thread.status == "closed" else "open",
            created_at=thread.created_at,
            closed_at=thread.closed_at,
            messages=[
                Communication(
                    id=m.id,
                    # The table's CHECK constraint holds it to these three.
                    sender=cast("MessageSender", m.sender),
                    sent_at=m.created_at,
                    body=m.body,
                    attachment_document_ids=[a.document_id for a in attachments.get(m.id, [])],
                )
                for m in messages
            ],
        )

    def _read_diagnoses(self, patient_id: str, user_id: str) -> list[Condition]:
        rows = [
            row
            for row in self._diagnoses.list_by_patient(patient_id, user_id)
            if row.get("deleted_at") is None
        ]
        rows.sort(key=lambda row: cast("datetime", row["assessed_at"]))
        return [_condition(row) for row in rows]


def _timezone(preferences: Mapping[str, UserPreferences], user_id: str) -> str:
    """The calendar owner's zone; the stored default when they never set one."""
    prefs = preferences.get(user_id)
    return prefs.timezone if prefs is not None else UserPreferences().timezone


def _appointment(
    appointment: Appointment, clinician_name: str | None, timezone: str
) -> ExportAppointment:
    return ExportAppointment(
        id=appointment.id,
        start=appointment.start_at,
        end=appointment.end_at,
        timezone=timezone,
        appointment_type=appointment.session_type,
        status=appointment.status,
        clinician_name=clinician_name,
        telehealth=is_telehealth(
            provider=appointment.provider,
            video_link=appointment.video_link,
            place_of_service=appointment.place_of_service,
        ),
        place_of_service=appointment.place_of_service,
        note_type=appointment.note_type,
        service_code=appointment.service_code,
        session_id=appointment.session_id,
    )


def _optional_text(row: Mapping[str, object], key: str) -> str | None:
    value = row.get(key)
    return str(value) if value is not None else None


def _medication(row: Mapping[str, object]) -> MedicationStatement:
    return MedicationStatement.model_validate(
        {
            "id": str(row["id"]),
            "drug_name": str(row["drug_name"]),
            "dose": str(row["dose"]),
            "frequency": _optional_text(row, "frequency"),
            "category": row.get("category"),
            "status": row["status"],
            "started_on": cast("date | None", row.get("started_at")),
            "stopped_on": cast("date | None", row.get("stopped_at")),
            "stop_reason": _optional_text(row, "stop_reason"),
            "notes": _optional_text(row, "notes"),
        }
    )


def _condition(row: Mapping[str, object]) -> Condition:
    code = _optional_text(row, "determined_icd10") or None
    meets = row.get("meets_criteria")
    return Condition(
        id=str(row["id"]),
        icd10_code=code,
        description=_optional_text(row, "diagnosis_label") or None,
        assessed_at=cast("datetime", row["assessed_at"]),
        status="confirmed" if code else "unconfirmed",
        instrument=str(row["instrument"]),
        meets_criteria=None if meets is None else bool(meets),
        session_id=_optional_text(row, "session_id"),
    )
