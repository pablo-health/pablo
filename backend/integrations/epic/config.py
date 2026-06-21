# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Configuration for the standalone Epic / MyChart puller.

All settings can be supplied via ``EPIC_``-prefixed environment
variables (or a local ``.env``) and overridden on the CLI.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Epic's public sandbox FHIR R4 base. Test patients and a self-service
# app registration live at https://fhir.epic.com — see this package's
# README for the credentials and the registration walkthrough.
EPIC_SANDBOX_R4_BASE = "https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4"

# Patient-facing read scopes. Standalone patient launch returns the
# authorizing patient's id in the token response; these scopes gate the
# resources we then pull. Keep in sync with PATIENT_RESOURCE_QUERIES.
DEFAULT_SCOPES = (
    "openid",
    "fhirUser",
    "offline_access",
    "patient/Patient.read",
    "patient/AllergyIntolerance.read",
    "patient/Condition.read",
    "patient/MedicationRequest.read",
    "patient/Observation.read",
    "patient/Immunization.read",
    "patient/Procedure.read",
    "patient/DiagnosticReport.read",
    "patient/DocumentReference.read",
    "patient/Encounter.read",
)

# System-level read scopes for the headless Backend Services flow. There
# is no patient context, so the caller names the patient explicitly.
DEFAULT_SYSTEM_SCOPES = (
    "system/Patient.read",
    "system/AllergyIntolerance.read",
    "system/Condition.read",
    "system/MedicationRequest.read",
    "system/Observation.read",
    "system/Immunization.read",
    "system/Procedure.read",
    "system/DiagnosticReport.read",
    "system/DocumentReference.read",
    "system/Encounter.read",
)


class EpicSettings(BaseSettings):
    """Runtime configuration for the Epic SMART on FHIR puller."""

    model_config = SettingsConfigDict(
        env_prefix="EPIC_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    auth_mode: Literal["patient", "backend"] = Field(
        default="patient",
        description="'patient' = interactive MyChart login; 'backend' = headless JWT credentials.",
    )
    fhir_base_url: str = Field(
        default=EPIC_SANDBOX_R4_BASE,
        description="FHIR R4 base URL of the Epic endpoint to pull from.",
    )
    client_id: str = Field(
        default="",
        description="OAuth2 client id of your registered (non-production) Epic app.",
    )
    redirect_host: str = Field(
        default="127.0.0.1",
        description="Loopback host the OAuth callback server binds to.",
    )
    redirect_port: int = Field(
        default=8765,
        description="Loopback port for the OAuth callback (must match the app's redirect URI).",
    )
    redirect_path: str = Field(
        default="/callback",
        description="Path of the OAuth redirect URI.",
    )
    scopes: str = Field(
        default=" ".join(DEFAULT_SCOPES),
        description="Space-delimited SMART on FHIR scopes to request.",
    )
    output_dir: Path = Field(
        default=Path("epic_export"),
        description="Directory that timestamped export runs are written under.",
    )
    request_timeout: float = Field(
        default=30.0,
        description="Per-request HTTP timeout, in seconds.",
    )
    callback_timeout: float = Field(
        default=300.0,
        description="How long to wait for the MyChart redirect before giving up, in seconds.",
    )

    # --- Backend Services (headless client-credentials) ---
    backend_scopes: str = Field(
        default=" ".join(DEFAULT_SYSTEM_SCOPES),
        description="Space-delimited system/* scopes requested in backend mode.",
    )
    backend_private_key_path: Path | None = Field(
        default=None,
        description="Path to the RSA private key (PEM) that signs the JWT client assertion.",
    )
    backend_kid: str = Field(
        default="",
        description="Key id (kid) of the public JWK registered with the Epic backend app.",
    )
    jwt_assertion_ttl: int = Field(
        default=300,
        description="Lifetime of the signed JWT client assertion, in seconds.",
    )

    @property
    def redirect_uri(self) -> str:
        """Full loopback redirect URI registered with the Epic app."""
        return f"http://{self.redirect_host}:{self.redirect_port}{self.redirect_path}"
