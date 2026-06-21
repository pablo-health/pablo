# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Named import profiles: breadth + depth presets for an Epic pull.

A profile bundles the resources to pull, the FHIR searches that pull them
(the depth axis — e.g. active-only for ``minimal``), and the matching
OAuth scopes, so consent and import stay in lockstep: we never request a
scope the profile won't use, and never pull a resource we didn't ask
consent for. ``exclude_sensitive`` is the sensitivity axis, enforced at
ingest time (see ``sensitivity``).
"""

from dataclasses import dataclass

from integrations.epic.exporter import ResourceQuery

_BASE_PATIENT_SCOPES = ("openid", "fhirUser", "offline_access")
_CORE_RESOURCES = ("Patient", "Condition", "MedicationRequest")
_CLINICAL_RESOURCES = ("AllergyIntolerance", "Observation", "Immunization", "Encounter")
_FULL_RESOURCES = ("Procedure", "DiagnosticReport", "DocumentReference")


@dataclass(frozen=True)
class ImportProfile:
    """A breadth/depth preset for what an import pulls and persists."""

    name: str
    resources: tuple[str, ...]
    queries: tuple[ResourceQuery, ...]
    exclude_sensitive: bool = True

    def scopes_for(self, auth_mode: str) -> str:
        """Build the space-delimited scope string for this profile + auth mode."""
        if auth_mode == "backend":
            return " ".join(f"system/{resource}.read" for resource in self.resources)
        patient_scopes = (f"patient/{resource}.read" for resource in self.resources)
        return " ".join((*_BASE_PATIENT_SCOPES, *patient_scopes))


_CLINICAL_QUERIES: tuple[ResourceQuery, ...] = (
    ResourceQuery("Condition", "Condition", {}),
    ResourceQuery("MedicationRequest", "MedicationRequest", {}),
    ResourceQuery("AllergyIntolerance", "AllergyIntolerance", {}),
    ResourceQuery("Observation", "Observation_laboratory", {"category": "laboratory"}),
    ResourceQuery("Observation", "Observation_vital-signs", {"category": "vital-signs"}),
    ResourceQuery("Immunization", "Immunization", {}),
    ResourceQuery("Encounter", "Encounter", {}),
)

MINIMAL = ImportProfile(
    name="minimal",
    resources=_CORE_RESOURCES,
    queries=(
        ResourceQuery("Condition", "Condition", {"clinical-status": "active"}),
        ResourceQuery("MedicationRequest", "MedicationRequest", {"status": "active"}),
    ),
)

CLINICAL = ImportProfile(
    name="clinical",
    resources=(*_CORE_RESOURCES, *_CLINICAL_RESOURCES),
    queries=_CLINICAL_QUERIES,
)

FULL = ImportProfile(
    name="full",
    resources=(*_CORE_RESOURCES, *_CLINICAL_RESOURCES, *_FULL_RESOURCES),
    queries=(
        *_CLINICAL_QUERIES,
        ResourceQuery("Procedure", "Procedure", {}),
        ResourceQuery("DiagnosticReport", "DiagnosticReport", {}),
        ResourceQuery("DocumentReference", "DocumentReference", {}),
    ),
)

PROFILES: dict[str, ImportProfile] = {p.name: p for p in (MINIMAL, CLINICAL, FULL)}
DEFAULT_PROFILE = "full"
