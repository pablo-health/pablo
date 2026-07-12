# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pluggable object-storage providers for file upload/download.

One interface, two backends:

* :class:`GcsFileStorage` — Google Cloud Storage (managed deployments).
  Delegates to the V4 signed-URL recipe in ``signed_upload.py``.
* :class:`S3FileStorage` — AWS S3 (or any S3-compatible endpoint such as
  MinIO / LocalStack via ``aws_s3_endpoint_url``). Requires the optional
  ``aws`` dependency group: ``poetry install --with aws``.

Selection is a configuration change (``FILE_STORAGE_PROVIDER=gcs|s3``),
not a code change — see :func:`file_storage_from_settings`. Callers hold
a :class:`FileStorageProvider` and never touch a cloud SDK directly.

Semantics shared by both backends:

* ``make_upload_url`` returns a presigned PUT URL bound to a single
  object name + content type. GCS additionally enforces ``max_bytes``
  at PUT time via ``x-goog-content-length-range``; S3 presigned PUTs
  cannot carry a length-range condition (that is a presigned-POST-only
  feature), so on S3 the size cap is enforced by the caller's
  finalize-time ``fetch_metadata`` re-check instead.
* ``fetch_metadata`` returns ``(size_bytes, content_type)`` or ``None``
  when the object does not exist.
* ``delete`` is idempotent — an already-missing object is a success.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..settings import Settings


class FileStorageProvider(ABC):
    """Object-storage operations needed by the file upload/download surfaces."""

    @abstractmethod
    def make_upload_url(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,
        ttl_seconds: int,
    ) -> str:
        """Presigned PUT URL for a browser-direct upload."""

    @abstractmethod
    def make_download_url(
        self,
        *,
        bucket: str,
        object_name: str,
        ttl_seconds: int,
        response_disposition: str | None = None,
    ) -> str:
        """Presigned GET URL for a 302-redirect download."""

    @abstractmethod
    def fetch_metadata(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> tuple[int, str | None] | None:
        """Return (size_bytes, content_type), or None if the object is missing."""

    @abstractmethod
    def download_bytes(self, *, bucket: str, object_name: str) -> bytes:
        """Download an object's bytes for in-process work (e.g. text extraction)."""

    @abstractmethod
    def delete(self, *, bucket: str, object_name: str) -> None:
        """Delete an object; already-gone is a success."""


class GcsFileStorage(FileStorageProvider):
    """Google Cloud Storage backend.

    ``client_factory`` is a test seam — production constructs a real
    ``google.cloud.storage.Client`` lazily so importing this module never
    requires GCP credentials.
    """

    def __init__(self, *, client_factory: Callable[[], Any] | None = None) -> None:
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        from google.cloud import storage  # type: ignore[attr-defined]

        return storage.Client()

    def make_upload_url(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,
        ttl_seconds: int,
    ) -> str:
        from .signed_upload import make_upload_url

        return make_upload_url(
            client=self._client(),
            bucket=bucket,
            object_name=object_name,
            content_type=content_type,
            max_bytes=max_bytes,
            ttl_seconds=ttl_seconds,
        )

    def make_download_url(
        self,
        *,
        bucket: str,
        object_name: str,
        ttl_seconds: int,
        response_disposition: str | None = None,
    ) -> str:
        from .signed_upload import make_download_url

        return make_download_url(
            client=self._client(),
            bucket=bucket,
            object_name=object_name,
            ttl_seconds=ttl_seconds,
            response_disposition=response_disposition,
        )

    def fetch_metadata(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> tuple[int, str | None] | None:
        from .signed_upload import fetch_blob_metadata

        return fetch_blob_metadata(
            client=self._client(),
            bucket=bucket,
            object_name=object_name,
        )

    def download_bytes(self, *, bucket: str, object_name: str) -> bytes:
        from .signed_upload import download_blob_bytes

        return download_blob_bytes(
            client=self._client(),
            bucket=bucket,
            object_name=object_name,
        )

    def delete(self, *, bucket: str, object_name: str) -> None:
        from .signed_upload import delete_blob

        delete_blob(client=self._client(), bucket=bucket, object_name=object_name)


class S3FileStorage(FileStorageProvider):
    """AWS S3 backend (SigV4 presigned URLs).

    Credentials come from boto3's default chain (env vars, instance/task
    role, ``~/.aws``) — never from Pablo settings. ``endpoint_url``
    supports S3-compatible stores (MinIO, LocalStack) for self-hosters.
    ``client_factory`` is a test seam, same shape as :class:`GcsFileStorage`.
    """

    def __init__(
        self,
        *,
        region: str | None = None,
        endpoint_url: str | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._region = region
        self._endpoint_url = endpoint_url
        self._client_factory = client_factory

    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise RuntimeError(
                "FILE_STORAGE_PROVIDER=s3 requires boto3 — install the "
                "optional aws dependency group: poetry install --with aws"
            ) from exc
        return boto3.client(
            "s3",
            region_name=self._region,
            endpoint_url=self._endpoint_url,
            config=Config(signature_version="s3v4"),
        )

    def make_upload_url(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,  # noqa: ARG002 — enforced at finalize; see module docstring
        ttl_seconds: int,
    ) -> str:
        url: str = self._client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": object_name,
                "ContentType": content_type,
            },
            ExpiresIn=ttl_seconds,
        )
        return url

    def make_download_url(
        self,
        *,
        bucket: str,
        object_name: str,
        ttl_seconds: int,
        response_disposition: str | None = None,
    ) -> str:
        params: dict[str, str] = {"Bucket": bucket, "Key": object_name}
        if response_disposition is not None:
            params["ResponseContentDisposition"] = response_disposition
        url: str = self._client().generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=ttl_seconds,
        )
        return url

    def fetch_metadata(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> tuple[int, str | None] | None:
        from botocore.exceptions import ClientError

        try:
            head = self._client().head_object(Bucket=bucket, Key=object_name)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return None
            raise
        return int(head["ContentLength"]), head.get("ContentType")

    def download_bytes(self, *, bucket: str, object_name: str) -> bytes:
        body = self._client().get_object(Bucket=bucket, Key=object_name)["Body"]
        data: bytes = body.read()
        return data

    def delete(self, *, bucket: str, object_name: str) -> None:
        # S3 DeleteObject is already idempotent — deleting a missing key
        # returns 204, matching the GCS backend's swallow-NotFound behavior.
        self._client().delete_object(Bucket=bucket, Key=object_name)


def file_storage_from_settings(settings: Settings) -> FileStorageProvider:
    """Construct the configured provider. GCS is the default."""
    if settings.file_storage_provider == "s3":
        return S3FileStorage(
            region=settings.aws_region,
            endpoint_url=settings.aws_s3_endpoint_url,
        )
    return GcsFileStorage()
