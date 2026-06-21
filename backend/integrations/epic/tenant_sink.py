# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Persist imported records into a practice tenant schema (clinician case).

Pablo is a Business Associate here; imported data becomes PHI in the
practice's tenant schema, written through the access-scoped repositories
so it inherits RLS (``has_patient_access``) and the soft-delete / purge
machinery. The triggering route owns the ``AuditService`` entry. Every row
is provenance-tagged so its Epic origin is always known.
"""

from datetime import date
from uuid import uuid4

from app.medications.repository import MedicationRepository
from app.models.patient import Patient
from app.repositories.patient import PatientRepository
from app.utcnow import utc_now

from integrations.epic.ingest import ImportedRecord, ImportResult
from integrations.epic.mappers import JsonDict, MappedCondition, MappedMedication

_PROVENANCE = "epic"


class TenantSink:
    """Persist into the practice tenant schema via Pablo's repositories.

    Creating the patient auto-grants the importing clinician primary
    access, and every write is RLS-checked. Provenance is recorded in the
    existing free-text fields (a dedicated source column is a follow-up
    migration).
    """

    def __init__(
        self,
        patient_repo: PatientRepository,
        medication_repo: MedicationRepository,
        user_id: str,
    ) -> None:
        self._patients = patient_repo
        self._medications = medication_repo
        self._user_id = user_id

    def write(self, record: ImportedRecord) -> ImportResult:
        patient = self._create_patient(record)
        created = 0
        for medication in record.medications:
            self._medications.create(self._medication_row(patient.id, medication), self._user_id)
            created += 1
        return ImportResult(
            patient_id=patient.id,
            medications_created=created,
            conditions_recorded=len(record.conditions),
            sensitive_skipped=record.sensitive_skipped,
        )

    def _create_patient(self, record: ImportedRecord) -> Patient:
        now = utc_now()
        mapped = record.patient
        patient = Patient(
            id=str(uuid4()),
            first_name=mapped.first_name,
            last_name=mapped.last_name,
            created_at=now,
            updated_at=now,
            email=mapped.email,
            phone=mapped.phone,
            date_of_birth=mapped.date_of_birth,
            diagnosis=_diagnosis_text(record.conditions),
        )
        return self._patients.create(patient, self._user_id)

    def _medication_row(self, patient_id: str, medication: MappedMedication) -> JsonDict:
        now = utc_now()
        return {
            "id": str(uuid4()),
            "patient_id": patient_id,
            "drug_name": medication.drug_name,
            "dose": medication.dose,
            "status": medication.status,
            "started_at": _as_date(medication.started_at),
            "stopped_at": None,
            "stop_reason": None,
            "notes": f"Imported from {_PROVENANCE} (MedicationRequest {medication.source_id})",
            "created_by": self._user_id,
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }


def _diagnosis_text(conditions: tuple[MappedCondition, ...]) -> str | None:
    labels = [c.label for c in conditions if c.label]
    return "; ".join(labels) if labels else None


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None
