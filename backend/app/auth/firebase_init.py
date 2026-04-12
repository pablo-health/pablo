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
