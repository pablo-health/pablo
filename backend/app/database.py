# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Database initialization and configuration.

Provides Firestore client setup with proper error handling and environment detection.
Supports both single-tenant (default DB) and multi-tenant (named DB per practice) modes.

All tenant clients share a single gRPC channel to avoid O(n) memory growth
with the number of tenants (~50-100 MB per channel).
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
def _get_base_client() -> Any:
    """Create the base Firestore client that owns the gRPC channel.

    All other clients share this client's transport to avoid creating
    a separate gRPC channel per tenant (~50-100 MB each).
    """
    _check_firestore_import()
    emulator_host = os.environ.get("FIRESTORE_EMULATOR_HOST")
    if emulator_host:
        return firestore.Client(project="pablo-dev")
    return firestore.Client()


def _make_client_sharing_channel(database: str) -> Any:
    """Create a Firestore client for a named database, sharing the base gRPC channel.

    The gRPC channel connects to firestore.googleapis.com — the database name
    is just a string in each request's resource path. So one channel serves all
    databases with zero additional connection overhead.
    """
    _check_firestore_import()
    base = _get_base_client()

    emulator_host = os.environ.get("FIRESTORE_EMULATOR_HOST")
    kwargs: dict[str, str] = {}
    if emulator_host:
        kwargs["project"] = "pablo-dev"
    if database != "(default)":
        kwargs["database"] = database

    client = firestore.Client(**kwargs)

    # Force the base client to initialize its gRPC channel, then share it
    _ = base._firestore_api  # triggers lazy channel creation
    client._firestore_api_internal = base._firestore_api_internal
    client._transport = base._transport

    return client


@lru_cache(maxsize=1)
def get_firestore_client() -> Any:
    """Get Firestore client for the (default) database.

    Returns cached client for connection pooling.
    Falls back to emulator if FIRESTORE_EMULATOR_HOST is set.
    """
    return _get_base_client()


@lru_cache(maxsize=256)
def get_tenant_firestore_client(database_name: str) -> Any:
    """Get or create a Firestore client for a specific tenant database.

    Clients are cached by database name. All share a single gRPC channel
    via the base client, so each cached entry is ~few KB (not ~50-100 MB).
    """
    if database_name == "(default)":
        return _get_base_client()
    return _make_client_sharing_channel(database_name)


@lru_cache(maxsize=1)
def get_admin_firestore_client() -> Any:
    """Get Firestore client for the admin (control plane) database.

    In single-tenant mode this is the same as (default).
    In multi-tenant mode this is the database specified by settings.admin_database.
    """
    settings = get_settings()
    if settings.admin_database == "(default)":
        return _get_base_client()
    return _make_client_sharing_channel(settings.admin_database)
