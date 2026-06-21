# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Assemble mapped FHIR records and define the import-sink seam.

This module is app-free: it turns an on-disk export into an
:class:`ImportedRecord` (applying the default sensitivity policy) and
declares the :class:`ImportSink` protocol. The concrete sinks live apart
so their dependencies stay isolated — ``tenant_sink`` (the practice DB)
and ``patient_store`` (the patient-owned encrypted store).
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from integrations.epic.mappers import (
    JsonDict,
    MappedCondition,
    MappedMedication,
    MappedPatient,
    bundle_resources,
    map_condition,
    map_medication,
    map_patient,
)
from integrations.epic.sensitivity import is_restricted


@dataclass(frozen=True)
class ImportedRecord:
    """One patient and the clinical resources pulled alongside them."""

    patient: MappedPatient
    medications: tuple[MappedMedication, ...]
    conditions: tuple[MappedCondition, ...]
    sensitive_skipped: int = 0


@dataclass(frozen=True)
class ImportResult:
    """Outcome of landing an :class:`ImportedRecord` into a sink."""

    patient_id: str
    medications_created: int
    conditions_recorded: int
    sensitive_skipped: int = 0


class ImportSink(Protocol):
    """A retention target for an imported record."""

    def write(self, record: ImportedRecord) -> ImportResult: ...


def build_record_from_export(run_dir: Path, *, exclude_sensitive: bool = True) -> ImportedRecord:
    """Assemble an :class:`ImportedRecord` from an on-disk export run dir.

    When ``exclude_sensitive`` is set (the default), DS4P / 42 CFR Part 2
    labeled resources are dropped before mapping and counted, rather than
    landing in the sink.
    """
    med_resources = bundle_resources(_read_json(run_dir / "MedicationRequest.json"))
    condition_resources = bundle_resources(_read_json(run_dir / "Condition.json"))

    skipped = 0
    if exclude_sensitive:
        kept_meds = [r for r in med_resources if not is_restricted(r)]
        kept_conditions = [r for r in condition_resources if not is_restricted(r)]
        skipped = (len(med_resources) - len(kept_meds)) + (
            len(condition_resources) - len(kept_conditions)
        )
        med_resources, condition_resources = kept_meds, kept_conditions

    return ImportedRecord(
        patient=map_patient(_read_json(run_dir / "Patient.json")),
        medications=tuple(map_medication(r) for r in med_resources),
        conditions=tuple(map_condition(r) for r in condition_resources),
        sensitive_skipped=skipped,
    )


def import_export(
    run_dir: Path, sink: ImportSink, *, exclude_sensitive: bool = True
) -> ImportResult:
    """Read an export run dir and land it into ``sink``."""
    return sink.write(build_record_from_export(run_dir, exclude_sensitive=exclude_sensitive))


def _read_json(path: Path) -> JsonDict:
    return json.loads(path.read_text(encoding="utf-8"))
