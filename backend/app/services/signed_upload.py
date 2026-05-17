# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""V4 signed PUT/GET URLs for browser-direct GCS uploads.

Used by both ``patient_documents_service`` and the additive
``/upload-audio/init|finalize`` endpoints. Keeps the signature
generation in one place — every caller passes the same headers and
``x-goog-content-length-range`` constraint so a forged sign attempt
gets rejected at GCS, not at our backend.

This module deliberately does NOT abstract the storage backend
(GCS vs S3 vs local) — per CLAUDE.md guardrails, that abstraction is
YAGNI until a self-hoster files an issue for a non-GCS backend. The
function exists to consolidate the signed-URL recipe, not to hide
GCS.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any


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
