# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""V4 signed PUT/GET URLs for browser-direct GCS uploads.

This is the GCS implementation detail behind
``file_storage.GcsFileStorage`` — callers should hold a
``FileStorageProvider`` (which also has an S3 backend for AWS
deployments) rather than importing these functions directly. Keeps
the signature generation in one place — every caller passes the same
headers and ``x-goog-content-length-range`` constraint so a forged
sign attempt gets rejected at GCS, not at our backend.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

logger = logging.getLogger(__name__)


def _iam_signing_kwargs() -> dict[str, Any]:
    """Return generate_signed_url kwargs for IAM-API-backed signing.

    V4 signed URLs require a private key locally to compute the
    signature. Cloud Run / GKE / GCE workload identities don't ship a
    private key — google.auth.default() returns a
    compute_engine.Credentials object that only carries a bearer
    token. Calling blob.generate_signed_url(version="v4") on those
    credentials raises AttributeError("you need a private key to sign
    credentials") and the route 500s.

    Workaround: pass service_account_email + access_token. The
    google-cloud-storage client then delegates the signature to the
    IAM signBlob API rather than computing it locally. The runtime
    SA needs roles/iam.serviceAccountTokenCreator ON ITSELF.

    Returns an empty dict when running with credentials that DO have
    a local private key (gcloud ADC refresh tokens, downloaded SA
    JSON keys) — those still self-sign without an IAM round-trip.

    Discovered via a downstream end-to-end test run.
    Without this, patient document upload has never worked end-to-end
    against any deployed environment.
    """
    import google.auth
    import google.auth.compute_engine
    import google.auth.exceptions
    import google.auth.transport.requests

    try:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except google.auth.exceptions.DefaultCredentialsError:
        # No ADC configured (typical for CI test runners). Callers in
        # this path use a fake GCS client so the empty kwargs are
        # harmless. Real deployments always have ADC.
        return {}

    request = google.auth.transport.requests.Request()

    # Cloud Run / GCE: credentials are ComputeEngineCredentials. They
    # don't carry a private key, and `service_account_email` defaults
    # to the literal string "default" until you look up the real
    # email via the metadata server.
    if isinstance(credentials, google.auth.compute_engine.Credentials):
        sa_email = credentials.service_account_email
        if not sa_email or sa_email == "default":
            from google.auth.compute_engine import _metadata

            try:
                info = _metadata.get_service_account_info(request)
            except Exception as exc:
                # Metadata lookup failed — log and fall through to
                # self-sign, which will raise the original informative
                # AttributeError instead of a confusing IAM 404.
                logger.warning(
                    "compute_engine SA email lookup failed; falling through "
                    "to local-sign which will fail with the canonical error: %s",
                    exc,
                )
                return {}
            sa_email = info.get("email") or ""
        if not sa_email:
            return {}
        credentials.refresh(request)
        return {
            "service_account_email": sa_email,
            "access_token": credentials.token,
        }

    # Anything else (user refresh tokens from gcloud ADC, SA JSON keys)
    # carries a private key locally — let the library self-sign.
    return {}


def make_upload_url(
    *,
    client: Any,
    bucket: str,
    object_name: str,
    content_type: str,
    max_bytes: int,
    ttl_seconds: int,
) -> str:
    """Generate a V4 signed PUT URL constrained by content-type + size.

    GCS enforces both constraints at object-write time: a request whose
    ``Content-Type`` doesn't match the signed value, or whose body
    exceeds ``max_bytes``, gets a 400 — no garbage lands in the
    bucket. ``ttl_seconds`` should be short (5 min by default); the
    URL is only useful for the immediate browser PUT.
    """
    blob = client.bucket(bucket).blob(object_name)
    signed: str = blob.generate_signed_url(
        version="v4",
        expiration=timedelta(seconds=ttl_seconds),
        method="PUT",
        content_type=content_type,
        headers={
            "x-goog-content-length-range": f"0,{max_bytes}",
        },
        **_iam_signing_kwargs(),
    )
    return signed


def make_download_url(
    *,
    client: Any,
    bucket: str,
    object_name: str,
    ttl_seconds: int,
    response_disposition: str | None = None,
) -> str:
    """Generate a V4 signed GET URL for a 302-redirect download.

    Short TTL is fine because the URL is consumed immediately by the
    redirect; the client never sees it directly. ``response_disposition``
    can force the browser into download-mode with a friendly filename.
    """
    blob = client.bucket(bucket).blob(object_name)
    kwargs: dict[str, Any] = {
        "version": "v4",
        "expiration": timedelta(seconds=ttl_seconds),
        "method": "GET",
        **_iam_signing_kwargs(),
    }
    if response_disposition is not None:
        kwargs["response_disposition"] = response_disposition
    signed: str = blob.generate_signed_url(**kwargs)
    return signed


def fetch_blob_metadata(
    *,
    client: Any,
    bucket: str,
    object_name: str,
) -> tuple[int, str | None] | None:
    """Return (size_bytes, content_type) for an object, or None if missing.

    Used by finalize endpoints to verify the upload actually completed
    and inspect what was written — defense-in-depth against a client
    that bypassed the signed-URL constraints (e.g. by stripping the
    Content-Type header on the PUT).
    """
    from google.cloud.exceptions import NotFound

    blob = client.bucket(bucket).blob(object_name)
    try:
        blob.reload()  # forces a metadata fetch; raises NotFound if absent
    except NotFound:
        return None
    if blob.size is None:
        return None
    size: int = int(blob.size)
    return size, blob.content_type


def download_blob_bytes(
    *,
    client: Any,
    bucket: str,
    object_name: str,
) -> bytes:
    """Download a GCS object's bytes. Used for in-process text extraction."""
    blob = client.bucket(bucket).blob(object_name)
    data: bytes = blob.download_as_bytes()
    return data


def delete_blob(
    *,
    client: Any,
    bucket: str,
    object_name: str,
) -> None:
    """Best-effort delete; ignores NotFound (already gone is a success)."""
    from google.cloud.exceptions import NotFound

    blob = client.bucket(bucket).blob(object_name)
    try:
        blob.delete()
    except NotFound:
        return
