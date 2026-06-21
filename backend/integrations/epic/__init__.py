# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Standalone Epic / MyChart patient-data puller (SMART on FHIR R4).

A self-contained CLI that runs the SMART on FHIR "standalone patient
launch" flow against an Epic FHIR endpoint (the public sandbox by
default), pulls the authorizing patient's records, and writes them to
disk as FHIR JSON. No PHI is sent into the Pablo backend — this is a
proof-of-concept for the import path that later integrations build on.

Run from the ``backend/`` directory (so ``integrations`` is importable)::

    poetry run python -m integrations.epic --help
"""

from integrations.epic.config import EpicSettings
from integrations.epic.errors import EpicAuthError, EpicConfigError
from integrations.epic.exporter import ExportSummary, export_patient_data
from integrations.epic.fhir_client import FhirClient
from integrations.epic.smart_auth import StandaloneLaunchFlow, TokenResponse

__all__ = [
    "EpicAuthError",
    "EpicConfigError",
    "EpicSettings",
    "ExportSummary",
    "FhirClient",
    "StandaloneLaunchFlow",
    "TokenResponse",
    "export_patient_data",
]
