# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Command-line entry point for the standalone Epic / MyChart puller."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import httpx

from integrations.epic.auth import TokenProvider
from integrations.epic.backend_services import BackendServicesAuth
from integrations.epic.config import EpicSettings
from integrations.epic.errors import EpicAuthError, EpicConfigError
from integrations.epic.exporter import export_patient_data
from integrations.epic.fhir_client import FhirClient, fetch_capability_statement
from integrations.epic.profiles import DEFAULT_PROFILE, PROFILES
from integrations.epic.smart_auth import StandaloneLaunchFlow, discover_smart_configuration

_REGISTER_HINT = (
    "Set EPIC_CLIENT_ID (or pass --client-id) to the client id of your "
    "registered Epic app. See backend/integrations/epic/README.md for the "
    "sandbox app-registration walkthrough."
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m integrations.epic",
        description="Pull a MyChart patient's records via SMART on FHIR and save them as JSON.",
    )
    parser.add_argument(
        "--auth-mode",
        choices=("patient", "backend"),
        help="'patient' = interactive MyChart login (default); 'backend' = headless JWT.",
    )
    parser.add_argument(
        "--profile",
        choices=tuple(PROFILES),
        default=DEFAULT_PROFILE,
        help=f"Import breadth: minimal | clinical | full (default: {DEFAULT_PROFILE}).",
    )
    parser.add_argument("--client-id", help="OAuth2 client id of your registered Epic app.")
    parser.add_argument("--fhir-base-url", help="FHIR R4 base URL (defaults to Epic's sandbox).")
    parser.add_argument(
        "--patient-id",
        help="FHIR patient id to pull (required in backend mode; patient mode derives it).",
    )
    parser.add_argument(
        "--private-key",
        type=Path,
        help="Path to the RSA private key (PEM) signing the JWT assertion (backend mode).",
    )
    parser.add_argument("--kid", help="Key id of the registered public JWK (backend mode).")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Directory to write the timestamped export run into (default: epic_export).",
    )
    parser.add_argument("--port", type=int, help="Loopback port for the OAuth callback.")
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the authorization URL instead of opening a browser.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Probe the FHIR endpoint (SMART discovery + /metadata) and exit, no login.",
    )
    return parser


def _resolve_settings(args: argparse.Namespace) -> EpicSettings:
    settings = EpicSettings()
    overrides: dict[str, object] = {}
    if args.auth_mode:
        overrides["auth_mode"] = args.auth_mode
    if args.client_id:
        overrides["client_id"] = args.client_id
    if args.fhir_base_url:
        overrides["fhir_base_url"] = args.fhir_base_url
    if args.private_key:
        overrides["backend_private_key_path"] = args.private_key
    if args.kid:
        overrides["backend_kid"] = args.kid
    if args.output_dir:
        overrides["output_dir"] = args.output_dir
    if args.port:
        overrides["redirect_port"] = args.port
    return settings.model_copy(update=overrides) if overrides else settings


def _build_provider(
    settings: EpicSettings, client: httpx.Client, *, open_browser: bool
) -> TokenProvider:
    if settings.auth_mode == "backend":
        return BackendServicesAuth(settings, client)
    if not settings.client_id:
        raise EpicConfigError(_REGISTER_HINT)
    return StandaloneLaunchFlow(settings, client, open_browser=open_browser)


def _run_check(settings: EpicSettings, client: httpx.Client) -> int:
    """Verify the FHIR endpoint is reachable before the interactive login."""
    print(f"Checking Epic FHIR endpoint: {settings.fhir_base_url}")
    smart = discover_smart_configuration(settings.fhir_base_url, client)
    print(f"  authorize endpoint: {smart.authorization_endpoint}")
    print(f"  token endpoint:     {smart.token_endpoint}")
    capability = fetch_capability_statement(settings.fhir_base_url, client)
    print(f"  FHIR version:       {capability.get('fhirVersion', 'unknown')}")
    print("\nConnectivity OK. Re-run without --check (with EPIC_CLIENT_ID set) to pull data.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _resolve_settings(args)
    profile = PROFILES[args.profile]
    # Keep consent in lockstep with breadth: request only the profile's scopes.
    scope_field = "backend_scopes" if settings.auth_mode == "backend" else "scopes"
    settings = settings.model_copy(update={scope_field: profile.scopes_for(settings.auth_mode)})

    with httpx.Client(timeout=settings.request_timeout, follow_redirects=False) as client:
        if args.check:
            return _run_check(settings, client)

        provider = _build_provider(settings, client, open_browser=not args.no_browser)
        grant = provider.acquire()
        print(f"\nGranted scopes: {grant.scope}\n")
        patient_id = grant.patient_id or args.patient_id
        if patient_id is None:
            raise EpicAuthError(
                "No patient context — pass --patient-id (required in backend mode), or in "
                "patient mode confirm the app requests standalone launch + patient/* scopes."
            )

        fhir = FhirClient(settings.fhir_base_url, grant.access_token, client)
        summary = export_patient_data(fhir, patient_id, settings.output_dir, profile.queries)

    # Don't echo the patient identifier to stdout (PHI hygiene); it's recorded
    # in the export's _export_metadata.json on disk instead.
    print(f"\nExport complete (profile: {profile.name})")
    print(f"Wrote {len(summary.counts)} resource files to: {summary.output_dir}")
    for label, count in summary.counts.items():
        print(f"  {label}: {count}")
    return 0


def run() -> None:
    """Console entry point: translate domain errors into a clean exit code."""
    try:
        raise SystemExit(main())
    except (EpicConfigError, EpicAuthError) as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    run()
