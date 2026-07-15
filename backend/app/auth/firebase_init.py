# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firebase Admin SDK initialization with Identity Platform support."""

import os
from functools import lru_cache

import firebase_admin
from firebase_admin import credentials

from ..settings import get_settings


@lru_cache
def initialize_firebase_app() -> firebase_admin.App:
    """Initialize and return the Firebase Admin SDK app (singleton).

    Credential strategy:
    - Emulator (FIREBASE_AUTH_EMULATOR_HOST set): no credentials needed
    - Workload Identity Federation (firebase_workload_identity): keyless creds
      for non-GCP hosts (e.g. AWS) that have no Application Default Credentials
    - Otherwise: Application Default Credentials (ADC)

    Note: Pablo's multi-tenancy is resolved from the user's email to a
    practice schema (see ``multi_tenancy_enabled``), not from Identity
    Platform tenants — this App is a plain single-pool token verifier.
    """
    settings = get_settings()
    project_id = settings.effective_firebase_project_id

    options: dict[str, str] = {}
    if project_id:
        options["projectId"] = project_id

    if os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
        # Emulator doesn't need real credentials
        return firebase_admin.initialize_app(options=options)

    if settings.firebase_workload_identity:
        # Non-GCP hosts (AWS) have no ADC — federate the runtime's cloud
        # identity to impersonate a service account instead.
        from .firebase_wif import WorkloadIdentityCredential  # noqa: PLC0415

        wif_cred = WorkloadIdentityCredential(
            audience=settings.firebase_wif_audience,
            sa_impersonation_url=settings.firebase_wif_sa_impersonation_url,
            project_id=project_id,
        )
        # external_account creds aren't a Signing type, so custom-token minting
        # (passkeys) needs an explicit serviceAccountId — that routes signing
        # through IAM signBlob using the WIF credential.
        options["serviceAccountId"] = wif_cred.service_account_email
        return firebase_admin.initialize_app(wif_cred, options)

    cred = credentials.ApplicationDefault()
    return firebase_admin.initialize_app(cred, options)
