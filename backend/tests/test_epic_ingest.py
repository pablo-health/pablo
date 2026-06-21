# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the Epic FHIR → Pablo mapping and ingestion sinks."""

import json
from pathlib import Path

import pytest
from app.medications.repository import (
    InMemoryMedicationRepository,
    PatientMedicationAccessDeniedError,
)
from app.repositories.patient import InMemoryPatientRepository
from integrations.epic.ingest import (
    ImportedRecord,
    PatientOwnedSink,
    TenantSink,
    build_record_from_export,
    import_export,
)
from integrations.epic.mappers import (
    map_condition,
    map_medication,
    map_patient,
)
from integrations.epic.profiles import MINIMAL, PROFILES
from integrations.epic.sensitivity import is_restricted

USER_ID = "11111111-1111-1111-1111-111111111111"

PATIENT_FHIR = {
    "resourceType": "Patient",
    "id": "pat-1",
    "name": [
        {"use": "official", "family": "Lopez", "given": ["Camila", "Maria"]},
        {"use": "nickname", "family": "Lopez", "given": ["Cami"]},
    ],
    "birthDate": "1987-09-12",
    "gender": "female",
    "telecom": [
        {"system": "phone", "value": "555-0100"},
        {"system": "email", "value": "camila@example.org"},
    ],
    "identifier": [
        {"system": "urn:other", "value": "X9"},
        {"type": {"coding": [{"code": "MR"}]}, "value": "MRN-42"},
    ],
}


def test_map_patient_prefers_official_name_and_mrn() -> None:
    mapped = map_patient(PATIENT_FHIR)
    assert (mapped.first_name, mapped.last_name) == ("Camila Maria", "Lopez")
    assert mapped.date_of_birth == "1987-09-12"
    assert mapped.email == "camila@example.org"
    assert mapped.phone == "555-0100"
    assert mapped.mrn == "MRN-42"


def test_map_medication_normalizes_status_and_date() -> None:
    mapped = map_medication(
        {
            "resourceType": "MedicationRequest",
            "id": "med-1",
            "status": "stopped",
            "authoredOn": "2026-01-04T10:00:00Z",
            "medicationCodeableConcept": {"text": "Sertraline 50 mg"},
            "dosageInstruction": [{"text": "1 tablet daily"}],
        }
    )
    assert mapped.drug_name == "Sertraline 50 mg"
    assert mapped.dose == "1 tablet daily"
    assert mapped.status == "discontinued"  # FHIR 'stopped' → Pablo 'discontinued'
    assert mapped.started_at == "2026-01-04"


def test_map_medication_falls_back_to_reference_and_blank_dose() -> None:
    mapped = map_medication(
        {
            "resourceType": "MedicationRequest",
            "id": "med-2",
            "status": "active",
            "medicationReference": {"display": "Lisinopril"},
        }
    )
    assert mapped.drug_name == "Lisinopril"
    assert mapped.dose == ""
    assert mapped.status == "active"


def test_map_condition_uses_text_then_coding() -> None:
    mapped = map_condition(
        {
            "resourceType": "Condition",
            "id": "cond-1",
            "code": {"coding": [{"code": "F32.1", "display": "Major depressive disorder"}]},
            "onsetDateTime": "2025-11-02",
        }
    )
    assert mapped.label == "Major depressive disorder"
    assert mapped.code == "F32.1"
    assert mapped.onset == "2025-11-02"


def _record() -> ImportedRecord:
    return ImportedRecord(
        patient=map_patient(PATIENT_FHIR),
        medications=(
            map_medication(
                {
                    "id": "med-1",
                    "status": "active",
                    "medicationCodeableConcept": {"text": "Sertraline 50 mg"},
                    "dosageInstruction": [{"text": "1 tablet daily"}],
                }
            ),
        ),
        conditions=(
            map_condition({"id": "c1", "code": {"text": "Anxiety"}}),
            map_condition({"id": "c2", "code": {"text": "Insomnia"}}),
        ),
    )


def test_tenant_sink_persists_patient_and_medications() -> None:
    patient_repo = InMemoryPatientRepository()
    medication_repo = InMemoryMedicationRepository()
    medication_repo.grant_all_access()  # in-memory repos don't share the grant table

    result = TenantSink(patient_repo, medication_repo, USER_ID).write(_record())

    assert result.medications_created == 1
    assert result.conditions_recorded == 2

    stored = patient_repo.get(result.patient_id, USER_ID)
    assert stored is not None
    assert stored.last_name == "Lopez"
    assert stored.diagnosis == "Anxiety; Insomnia"  # conditions folded into diagnosis

    meds = medication_repo.list_by_patient(result.patient_id, USER_ID)
    assert len(meds) == 1
    assert meds[0]["drug_name"] == "Sertraline 50 mg"
    assert "Imported from epic" in str(meds[0]["notes"])  # provenance recorded


def test_tenant_sink_create_auto_grants_importer_access() -> None:
    # The medication repo is NOT pre-granted; access must come from the
    # patient create auto-grant path the Postgres repos share. The in-memory
    # medication repo can't see that cross-repo grant, so a write without an
    # explicit grant is expected to be denied — documenting the boundary.
    patient_repo = InMemoryPatientRepository()
    medication_repo = InMemoryMedicationRepository()
    record = _record()

    with pytest.raises(PatientMedicationAccessDeniedError):
        TenantSink(patient_repo, medication_repo, USER_ID).write(record)


def test_patient_owned_sink_is_not_yet_implemented() -> None:
    with pytest.raises(NotImplementedError):
        PatientOwnedSink().write(_record())


def test_profile_scopes_track_resources() -> None:
    patient_scopes = MINIMAL.scopes_for("patient")
    assert "patient/Patient.read" in patient_scopes
    assert "patient/MedicationRequest.read" in patient_scopes
    assert "patient/Procedure.read" not in patient_scopes  # not in the minimal profile
    assert "offline_access" in patient_scopes

    backend_scopes = MINIMAL.scopes_for("backend")
    assert "system/Patient.read" in backend_scopes
    assert "patient/" not in backend_scopes


def test_profiles_widen_from_minimal_to_full() -> None:
    minimal = set(PROFILES["minimal"].resources)
    clinical = set(PROFILES["clinical"].resources)
    full = set(PROFILES["full"].resources)
    assert minimal < clinical < full


def test_is_restricted_flags_part2_and_confidentiality() -> None:
    assert is_restricted({"meta": {"security": [{"code": "ETH"}]}})  # substance abuse
    assert is_restricted({"meta": {"security": [{"code": "R"}]}})  # restricted
    assert not is_restricted({"meta": {"security": [{"code": "N"}]}})  # normal
    assert not is_restricted({})


def test_build_record_excludes_sensitive_resources(tmp_path: Path) -> None:
    (tmp_path / "Patient.json").write_text(json.dumps(PATIENT_FHIR))
    (tmp_path / "MedicationRequest.json").write_text(
        json.dumps(
            {
                "resourceType": "Bundle",
                "entry": [
                    {"resource": {"id": "m1", "status": "active",
                                  "medicationCodeableConcept": {"text": "Sertraline"}}},
                    {"resource": {"id": "m2", "status": "active",
                                  "meta": {"security": [{"code": "ETH"}]},
                                  "medicationCodeableConcept": {"text": "Buprenorphine"}}},
                ],
            }
        )
    )
    (tmp_path / "Condition.json").write_text(json.dumps({"resourceType": "Bundle", "entry": []}))

    excluded = build_record_from_export(tmp_path)
    assert len(excluded.medications) == 1  # the Part 2 med is dropped
    assert excluded.sensitive_skipped == 1
    assert excluded.medications[0].drug_name == "Sertraline"

    included = build_record_from_export(tmp_path, exclude_sensitive=False)
    assert len(included.medications) == 2
    assert included.sensitive_skipped == 0


def test_import_export_reads_run_dir(tmp_path: Path) -> None:
    (tmp_path / "Patient.json").write_text(json.dumps(PATIENT_FHIR))
    (tmp_path / "MedicationRequest.json").write_text(
        json.dumps(
            {
                "resourceType": "Bundle",
                "entry": [
                    {
                        "resource": {
                            "id": "med-1",
                            "status": "active",
                            "medicationCodeableConcept": {"text": "Sertraline"},
                            "dosageInstruction": [{"text": "1 tab"}],
                        }
                    }
                ],
            }
        )
    )
    (tmp_path / "Condition.json").write_text(json.dumps({"resourceType": "Bundle", "entry": []}))

    record = build_record_from_export(tmp_path)
    assert record.patient.last_name == "Lopez"
    assert len(record.medications) == 1

    patient_repo = InMemoryPatientRepository()
    medication_repo = InMemoryMedicationRepository()
    medication_repo.grant_all_access()
    result = import_export(tmp_path, TenantSink(patient_repo, medication_repo, USER_ID))
    assert result.medications_created == 1
