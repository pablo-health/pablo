# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Land mapped FHIR records into a retention sink.

The mapping (``mappers``) is shared; *where the data lands* is pluggable
via :class:`ImportSink`, because the two import use cases have different
retention/legal models:

* :class:`TenantSink` — the clinician / prescriber case. Pablo is a
  Business Associate; imported data becomes PHI in the practice's tenant
  schema, written through the existing repositories so it inherits RLS
  (``has_patient_access``) and the soft-delete / purge machinery. The
  triggering route is responsible for the ``AuditService`` entry.
* :class:`PatientOwnedSink` — the patient-support case. Retention is the
  patient's, under a PHR (FTC Health Breach Notification Rule) model, not
  a BAA. Deliberately a stub until the encrypted, patient-controlled,
  TTL'd store and its consent model are finalized.

Every row is provenance-tagged so a record's Epic origin is always known.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.medications.repository import MedicationRepository
from app.models.patient import Patient
from app.repositories.patient import PatientRepository
from app.utcnow import utc_now

from integrations.epic.mappers import (
    JsonDict,
    MappedCondition,
    MappedMedication,
    MappedPatient,
    map_bundle,
    map_condition,
    map_medication,
    map_patient,
)

_PROVENANCE = "epic"


@dataclass(frozen=True)
class ImportedRecord:
    """One patient and the clinical resources pulled alongside them."""

    patient: MappedPatient
    medications: tuple[MappedMedication, ...]
    conditions: tuple[MappedCondition, ...]


@dataclass(frozen=True)
class ImportResult:
    """Outcome of landing an :class:`ImportedRecord` into a sink."""

    patient_id: str
    medications_created: int
    conditions_recorded: int


class ImportSink(Protocol):
    """A retention target for an imported record."""

    def write(self, record: ImportedRecord) -> ImportResult: ...


class TenantSink:
    """Persist into the practice tenant schema via Pablo's repositories.

    Reuses the access-scoped repositories, so creating the patient
    auto-grants the importing clinician primary access and every write is
    RLS-checked. Provenance is recorded in the existing free-text fields
    (a dedicated source column is a follow-up migration).
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


class PatientOwnedSink:
    """Patient-controlled PHR store (FTC HBNR model) — not yet implemented.

    Unlike :class:`TenantSink`, this must persist under the patient's own
    control: encrypted at rest, TTL'd, one-tap delete, PHR consent rather
    than a BAA. Left as a stub until that storage + consent model is
    finalized so the sink seam exists without committing to a design.
    """

    def write(self, record: ImportedRecord) -> ImportResult:
        raise NotImplementedError(
            "PatientOwnedSink is not implemented — the patient-owned PHR store "
            "(encrypted, TTL'd, patient-purgeable) is still being designed."
        )


def build_record_from_export(run_dir: Path) -> ImportedRecord:
    """Assemble an :class:`ImportedRecord` from an on-disk export run dir."""
    patient = map_patient(_read_json(run_dir / "Patient.json"))
    medications = map_bundle(_read_json(run_dir / "MedicationRequest.json"), map_medication)
    conditions = map_bundle(_read_json(run_dir / "Condition.json"), map_condition)
    return ImportedRecord(
        patient=patient,
        medications=tuple(medications),
        conditions=tuple(conditions),
    )


def import_export(run_dir: Path, sink: ImportSink) -> ImportResult:
    """Read an export run dir and land it into ``sink``."""
    return sink.write(build_record_from_export(run_dir))


def _diagnosis_text(conditions: tuple[MappedCondition, ...]) -> str | None:
    labels = [c.label for c in conditions if c.label]
    return "; ".join(labels) if labels else None


def _as_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _read_json(path: Path) -> JsonDict:
    return json.loads(path.read_text(encoding="utf-8"))
