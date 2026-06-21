# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pure FHIR R4 → neutral Pablo-shaped records.

These functions are deliberately dependency-free (stdlib only) and know
nothing about Pablo's database or the import sink. They turn raw FHIR
resources into small frozen dataclasses that a sink then persists, so the
mapping is trivially unit-testable in isolation.
"""

from dataclasses import dataclass
from typing import Any

JsonDict = dict[str, Any]

# FHIR MedicationRequest.status → Pablo patient_medications.status, which is
# constrained to {active, discontinued, on_hold} by a CHECK constraint.
_MED_STATUS_MAP = {
    "active": "active",
    "on-hold": "on_hold",
    "stopped": "discontinued",
    "completed": "discontinued",
    "cancelled": "discontinued",
    "entered-in-error": "discontinued",
    "draft": "active",
    "unknown": "active",
}


@dataclass(frozen=True)
class MappedPatient:
    """A FHIR Patient reduced to the fields Pablo's PatientRow carries."""

    source_id: str
    first_name: str
    last_name: str
    date_of_birth: str | None
    email: str | None
    phone: str | None
    gender: str | None
    mrn: str | None


@dataclass(frozen=True)
class MappedMedication:
    """A FHIR MedicationRequest reduced to Pablo's medication fields."""

    source_id: str
    drug_name: str
    dose: str
    status: str
    started_at: str | None


@dataclass(frozen=True)
class MappedCondition:
    """A FHIR Condition reduced to a label + code for the diagnosis field."""

    source_id: str
    label: str
    code: str | None
    onset: str | None


def map_patient(resource: JsonDict) -> MappedPatient:
    """Map a single FHIR Patient resource."""
    first, last = _human_name(resource.get("name", []))
    telecom = resource.get("telecom", [])
    return MappedPatient(
        source_id=resource.get("id", ""),
        first_name=first,
        last_name=last,
        date_of_birth=resource.get("birthDate"),
        email=_telecom_value(telecom, "email"),
        phone=_telecom_value(telecom, "phone"),
        gender=resource.get("gender"),
        mrn=_mrn(resource.get("identifier", [])),
    )


def map_medication(resource: JsonDict) -> MappedMedication:
    """Map a single FHIR MedicationRequest resource."""
    return MappedMedication(
        source_id=resource.get("id", ""),
        drug_name=_medication_name(resource),
        dose=_dose_text(resource),
        status=_MED_STATUS_MAP.get(resource.get("status", ""), "active"),
        started_at=_as_date(resource.get("authoredOn")),
    )


def map_condition(resource: JsonDict) -> MappedCondition:
    """Map a single FHIR Condition resource."""
    code = resource.get("code", {})
    coding = _first_coding(code)
    return MappedCondition(
        source_id=resource.get("id", ""),
        label=code.get("text") or coding.get("display", "") or coding.get("code", ""),
        code=coding.get("code"),
        onset=_as_date(resource.get("onsetDateTime")),
    )


def map_bundle(bundle: JsonDict, mapper: Any) -> list[Any]:
    """Apply ``mapper`` to every resource entry in a searchset Bundle."""
    return [mapper(entry["resource"]) for entry in bundle.get("entry", []) if "resource" in entry]


# --- FHIR field extractors -------------------------------------------------


def _human_name(names: list[JsonDict]) -> tuple[str, str]:
    """Return ``(first, last)`` from a FHIR HumanName list, preferring official."""
    if not names:
        return "", ""
    chosen = next((n for n in names if n.get("use") == "official"), names[0])
    given = chosen.get("given", [])
    first = " ".join(given) if given else chosen.get("text", "")
    return first, chosen.get("family", "")


def _telecom_value(telecom: list[JsonDict], system: str) -> str | None:
    for contact in telecom:
        if contact.get("system") == system and contact.get("value"):
            return str(contact["value"])
    return None


def _mrn(identifiers: list[JsonDict]) -> str | None:
    """Pick the medical record number (type code ``MR``), else the first id."""
    for identifier in identifiers:
        codings = identifier.get("type", {}).get("coding", [])
        if any(c.get("code") == "MR" for c in codings) and identifier.get("value"):
            return str(identifier["value"])
    for identifier in identifiers:
        if identifier.get("value"):
            return str(identifier["value"])
    return None


def _medication_name(resource: JsonDict) -> str:
    concept = resource.get("medicationCodeableConcept", {})
    coding = _first_coding(concept)
    name = concept.get("text") or coding.get("display")
    if name:
        return str(name)
    reference = resource.get("medicationReference", {})
    return str(reference.get("display", "") or "")


def _dose_text(resource: JsonDict) -> str:
    instructions = resource.get("dosageInstruction", [])
    for instruction in instructions:
        if instruction.get("text"):
            return str(instruction["text"])
    return ""


def _first_coding(concept: JsonDict) -> JsonDict:
    codings = concept.get("coding", [])
    return codings[0] if codings else {}


def _as_date(value: str | None) -> str | None:
    """Trim a FHIR dateTime to its date part (``2026-06-21T09:00:00Z`` → ``2026-06-21``)."""
    if not value:
        return None
    return value.split("T", 1)[0]
