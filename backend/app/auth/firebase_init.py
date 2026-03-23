# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Firebase Admin SDK initialization with Identity Platform support."""

import logging
import os
from functools import lru_cache

import firebase_admin
import google.auth
import google.auth.transport.requests
import requests  # type: ignore[import-untyped]
from firebase_admin import credentials, tenant_mgt

from ..settings import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def initialize_firebase_app() -> firebase_admin.App:
    """Initialize and return the Firebase Admin SDK app (singleton).

    Credential strategy:
    - Emulator (FIREBASE_AUTH_EMULATOR_HOST set): no credentials needed
    - Otherwise: Application Default Credentials (ADC)

    Identity Platform multi-tenancy is configured at the GCP project level.
    The same App instance supports both single-tenant and multi-tenant modes —
    tenant_mgt functions use the App's credentials automatically.
    """
    settings = get_settings()
    project_id = settings.effective_firebase_project_id

    options: dict[str, str] = {}
    if project_id:
        options["projectId"] = project_id

    if os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
        # Emulator doesn't need real credentials
        return firebase_admin.initialize_app(options=options)

    cred = credentials.ApplicationDefault()
    return firebase_admin.initialize_app(cred, options)


def list_tenants() -> list[tenant_mgt.Tenant]:
    """List all Identity Platform tenants.

    Requires multi-tenancy to be enabled in the GCP project.
    Used by admin APIs for tenant management.
    """
    initialize_firebase_app()
    page = tenant_mgt.list_tenants()
    return list(page.iterate_all())


def get_tenant(tenant_id: str) -> tenant_mgt.Tenant:
    """Get an Identity Platform tenant by ID."""
    initialize_firebase_app()
    return tenant_mgt.get_tenant(tenant_id)


def create_tenant(
    display_name: str,
    allow_password_sign_up: bool = True,
) -> tenant_mgt.Tenant:
    """Create a new Identity Platform tenant.

    Args:
        display_name: Human-readable practice name.
        allow_password_sign_up: Whether to allow email/password auth.
    """
    initialize_firebase_app()
    return tenant_mgt.create_tenant(
        display_name=display_name,
        allow_password_sign_up=allow_password_sign_up,
        enable_email_link_sign_in=False,
    )


def enable_google_sign_in(tenant_id: str) -> None:
    """Enable Google OAuth on a tenant by copying the project-level config.

    Uses the Identity Platform REST API since the Admin SDK doesn't support
    per-tenant IdP configuration.
    """
    settings = get_settings()
    project_id = settings.gcp_project_id

    creds, _ = google.auth.default(  # type: ignore[no-untyped-call]
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]

    base_url = f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}"
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "x-goog-user-project": project_id,
        "Content-Type": "application/json",
    }

    # Fetch project-level Google OAuth config (client ID + secret)
    resp = requests.get(
        f"{base_url}/defaultSupportedIdpConfigs/google.com",
        headers=headers,
        timeout=10,
    )
    resp.raise_for_status()
    project_config = resp.json()

    # Create the same config on the tenant
    resp = requests.post(
        f"{base_url}/tenants/{tenant_id}/defaultSupportedIdpConfigs",
        headers=headers,
        params={"idpId": "google.com"},
        json={
            "enabled": True,
            "clientId": project_config["clientId"],
            "clientSecret": project_config["clientSecret"],
        },
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Enabled Google sign-in on tenant %s", tenant_id)


def enable_mfa(tenant_id: str) -> None:
    """Enable TOTP MFA on a tenant by copying the project-level config."""
    settings = get_settings()
    project_id = settings.gcp_project_id

    creds, _ = google.auth.default(  # type: ignore[no-untyped-call]
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]

    resp = requests.patch(
        f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/tenants/{tenant_id}",
        headers={
            "Authorization": f"Bearer {creds.token}",
            "x-goog-user-project": project_id,
            "Content-Type": "application/json",
        },
        params={"updateMask": "mfaConfig"},
        json={
            "mfaConfig": {
                "state": "ENABLED",
                "providerConfigs": [
                    {
                        "totpProviderConfig": {"adjacentIntervals": 5},
                        "state": "ENABLED",
                    }
                ],
            }
        },
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Enabled MFA on tenant %s", tenant_id)


def enable_email_inheritance(tenant_id: str) -> None:
    """Enable email config inheritance so tenant uses project-level SMTP and templates.

    Without this, tenants default to generic Firebase email delivery even when
    custom SMTP and branded templates are configured at the project level.
    """
    settings = get_settings()
    project_id = settings.gcp_project_id

    creds, _ = google.auth.default(  # type: ignore[no-untyped-call]
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(google.auth.transport.requests.Request())  # type: ignore[no-untyped-call]

    resp = requests.patch(
        f"https://identitytoolkit.googleapis.com/v2/projects/{project_id}/tenants/{tenant_id}",
        headers={
            "Authorization": f"Bearer {creds.token}",
            "x-goog-user-project": project_id,
            "Content-Type": "application/json",
        },
        params={"updateMask": "inheritance.emailSendingConfig"},
        json={"inheritance": {"emailSendingConfig": True}},
        timeout=10,
    )
    resp.raise_for_status()
    logger.info("Enabled email inheritance on tenant %s", tenant_id)


def delete_tenant(tenant_id: str) -> None:
    """Delete an Identity Platform tenant (used for rollback on provisioning failure)."""
    initialize_firebase_app()
    tenant_mgt.delete_tenant(tenant_id)
