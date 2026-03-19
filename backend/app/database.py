# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Database initialization and configuration.

Provides Firestore client setup with proper error handling and environment detection.
Supports both single-tenant (default DB) and multi-tenant (named DB per practice) modes.
"""

import os
from functools import lru_cache
from typing import Any

from .settings import get_settings

try:
    from google.cloud import firestore
except ImportError:
    firestore = None


def _check_firestore_import() -> None:
    if firestore is None:
        raise ImportError(
            "google-cloud-firestore is required for Firestore support. "
            "Install with: pip install google-cloud-firestore"
        )


@lru_cache(maxsize=1)
def get_firestore_client() -> Any:
    """Get Firestore client for the (default) database.

    Returns cached client for connection pooling.
    Falls back to emulator if FIRESTORE_EMULATOR_HOST is set.
    """
    _check_firestore_import()

    try:
        emulator_host = os.environ.get("FIRESTORE_EMULATOR_HOST")
        if emulator_host:
            return firestore.Client(project="pablo-dev")
        return firestore.Client()
    except Exception as err:
        raise RuntimeError("Failed to initialize Firestore client") from err


@lru_cache(maxsize=256)
def get_tenant_firestore_client(database_name: str) -> Any:
    """Get or create a Firestore client for a specific tenant database.

    Clients are cached by database name for connection pooling.
    """
    _check_firestore_import()

    try:
        emulator_host = os.environ.get("FIRESTORE_EMULATOR_HOST")
        kwargs: dict[str, str] = {}
        if emulator_host:
            kwargs["project"] = "pablo-dev"
        if database_name != "(default)":
            kwargs["database"] = database_name
        return firestore.Client(**kwargs)
    except Exception as err:
        raise RuntimeError(
            f"Failed to initialize Firestore client for {database_name}"
        ) from err


@lru_cache(maxsize=1)
def get_admin_firestore_client() -> Any:
    """Get Firestore client for the admin (control plane) database.

    In single-tenant mode this is the same as (default).
    In multi-tenant mode this is the database specified by settings.admin_database.
    """
    _check_firestore_import()
    settings = get_settings()

    try:
        emulator_host = os.environ.get("FIRESTORE_EMULATOR_HOST")
        project = "pablo-dev" if emulator_host else None
        kwargs: dict[str, str] = {}
        if project:
            kwargs["project"] = project
        if settings.admin_database != "(default)":
            kwargs["database"] = settings.admin_database
        return firestore.Client(**kwargs)
    except Exception as err:
        raise RuntimeError("Failed to initialize admin Firestore client") from err
