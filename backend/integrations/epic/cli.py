# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Command-line entry point for the standalone Epic / MyChart puller."""

import argparse
from collections.abc import Sequence
from pathlib import Path

import httpx

from integrations.epic.config import EpicSettings
from integrations.epic.errors import EpicAuthError, EpicConfigError
from integrations.epic.exporter import export_patient_data
from integrations.epic.fhir_client import FhirClient
from integrations.epic.smart_auth import StandaloneLaunchFlow

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
    parser.add_argument("--client-id", help="OAuth2 client id of your registered Epic app.")
    parser.add_argument("--fhir-base-url", help="FHIR R4 base URL (defaults to Epic's sandbox).")
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
    return parser


def _resolve_settings(args: argparse.Namespace) -> EpicSettings:
    settings = EpicSettings()
    overrides: dict[str, object] = {}
    if args.client_id:
        overrides["client_id"] = args.client_id
    if args.fhir_base_url:
        overrides["fhir_base_url"] = args.fhir_base_url
    if args.output_dir:
        overrides["output_dir"] = args.output_dir
    if args.port:
        overrides["redirect_port"] = args.port
    return settings.model_copy(update=overrides) if overrides else settings


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = _resolve_settings(args)

    if not settings.client_id:
        raise EpicConfigError(_REGISTER_HINT)

    with httpx.Client(timeout=settings.request_timeout, follow_redirects=False) as client:
        flow = StandaloneLaunchFlow(settings, client)
        token = flow.authorize(open_browser=not args.no_browser)
        if token.patient_id is None:
            raise EpicAuthError(
                "Token response did not include a patient context — confirm the app is "
                "registered for standalone patient launch with the patient/* scopes."
            )

        fhir = FhirClient(settings.fhir_base_url, token.access_token, client)
        summary = export_patient_data(fhir, token.patient_id, settings.output_dir)

    print(f"\nExport complete for patient {token.patient_id}")
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
