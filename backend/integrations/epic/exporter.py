# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pull a patient's FHIR resources and write them to disk as JSON."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from integrations.epic.fhir_client import FhirClient, JsonDict


@dataclass(frozen=True)
class ResourceQuery:
    """A patient-scoped FHIR search to run and the file label to save it under."""

    resource_type: str
    label: str
    params: dict[str, str]


# Patient-scoped searches. The patient reference is injected at runtime,
# so each query only carries its extra filters. Observation is split by
# category because Epic requires a category on Observation searches.
PATIENT_RESOURCE_QUERIES: tuple[ResourceQuery, ...] = (
    ResourceQuery("AllergyIntolerance", "AllergyIntolerance", {}),
    ResourceQuery("Condition", "Condition", {}),
    ResourceQuery("MedicationRequest", "MedicationRequest", {}),
    ResourceQuery("Observation", "Observation_laboratory", {"category": "laboratory"}),
    ResourceQuery("Observation", "Observation_vital-signs", {"category": "vital-signs"}),
    ResourceQuery("Immunization", "Immunization", {}),
    ResourceQuery("Procedure", "Procedure", {}),
    ResourceQuery("DiagnosticReport", "DiagnosticReport", {}),
    ResourceQuery("DocumentReference", "DocumentReference", {}),
    ResourceQuery("Encounter", "Encounter", {}),
)


@dataclass(frozen=True)
class ExportSummary:
    """Where an export landed and how many resources each query yielded."""

    output_dir: Path
    counts: dict[str, int]


def export_patient_data(
    client: FhirClient,
    patient_id: str,
    output_dir: Path,
    queries: tuple[ResourceQuery, ...] = PATIENT_RESOURCE_QUERIES,
) -> ExportSummary:
    """Pull the patient record + scoped searches into a timestamped run dir."""
    run_dir = output_dir / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    patient = client.read("Patient", patient_id)
    _write_json(run_dir / "Patient.json", patient)
    counts: dict[str, int] = {"Patient": 1}

    for query in queries:
        bundle = client.search(query.resource_type, {"patient": patient_id, **query.params})
        _write_json(run_dir / f"{query.label}.json", bundle)
        counts[query.label] = int(bundle.get("total", 0))

    _write_json(
        run_dir / "_export_metadata.json",
        {
            "patient_id": patient_id,
            "exported_at": datetime.now(UTC).isoformat(),
            "counts": counts,
        },
    )
    return ExportSummary(output_dir=run_dir, counts=counts)


def _write_json(path: Path, data: JsonDict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
