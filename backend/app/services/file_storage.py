# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Pluggable object-storage providers for file upload/download.

One interface, three backends:

* :class:`GcsFileStorage` — Google Cloud Storage (managed deployments).
  Delegates to the V4 signed-URL recipe in ``signed_upload.py``.
* :class:`S3FileStorage` — AWS S3 (or any S3-compatible endpoint such as
  MinIO / LocalStack via ``aws_s3_endpoint_url``). Requires the optional
  ``aws`` dependency group: ``poetry install --with aws``.
* :class:`LocalFileStorage` — local filesystem (self-hosted deployments;
  e.g. an EFS/NFS mount). Server-side byte ops only — it cannot mint
  browser-direct upload/download URLs, so it serves proxied surfaces
  like compliance documents, not the signed-URL surfaces.

Selection is a configuration change (``FILE_STORAGE_PROVIDER=gcs|s3``),
not a code change — see :func:`file_storage_from_settings`. Callers hold
a :class:`FileStorageProvider` and never touch a cloud SDK directly.

Semantics shared by both backends:

* ``make_upload_target`` returns an :class:`UploadTarget` bound to a
  single object name + content type, with ``max_bytes`` enforced by the
  storage service at upload time. GCS uses a signed PUT URL with an
  ``x-goog-content-length-range`` header; S3 uses a presigned POST
  (multipart form) because only POST policies can carry a
  ``content-length-range`` condition. The client dispatches on
  ``UploadTarget.method``.
* ``fetch_metadata`` returns ``(size_bytes, content_type)`` or ``None``
  when the object does not exist.
* ``delete`` is idempotent — an already-missing object is a success.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..settings import Settings

# Read granularity for streaming uploads. 1 MiB is a whole multiple of GCS's
# 256 KiB resumable-chunk requirement, so it doubles as the GCS resumable
# ``chunk_size``. Streaming in fixed chunks keeps a large audio file (a long
# session runs to hundreds of MB per channel) off the heap — the whole point
# of ``upload_stream`` versus reading the body into a single ``bytes``.
_STREAM_CHUNK_SIZE = 1024 * 1024


class FileTooLargeError(Exception):
    """Raised by ``upload_stream`` when the source exceeds ``max_bytes``.

    Carries the cap so callers can turn it into a 413 with a useful message.
    """

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        super().__init__(f"upload exceeds maximum of {max_bytes} bytes")


class _CappedReader:
    """Wrap a binary file object, raising once cumulative reads exceed a cap.

    Passes ``seek``/``tell`` through so a storage SDK that probes the stream
    size (GCS) still works; the running byte count is what actually enforces
    the limit, so an oversized stream is rejected mid-transfer rather than
    after it has all landed. Deliberately minimal — only the surface the
    upload SDKs touch.
    """

    def __init__(self, fileobj: BinaryIO, max_bytes: int) -> None:
        self._fileobj = fileobj
        self._max_bytes = max_bytes
        self._read = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fileobj.read(size)
        self._read += len(chunk)
        if self._read > self._max_bytes:
            raise FileTooLargeError(self._max_bytes)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._fileobj.seek(offset, whence)

    def tell(self) -> int:
        return self._fileobj.tell()

    def seekable(self) -> bool:
        return getattr(self._fileobj, "seekable", lambda: False)()


@dataclass(frozen=True)
class UploadTarget:
    """Self-describing recipe for a browser-direct upload.

    The provider fully specifies the request; the client executes it
    without knowing which backend produced it:

    * ``method="PUT"`` (GCS): send the file as the raw body with
      ``headers`` attached — they carry the signed Content-Type and
      size-range constraints. ``fields`` is empty.
    * ``method="POST"`` (S3): send ``multipart/form-data`` with
      ``fields`` (the signed policy) as the leading form entries and
      the file as the last part. ``headers`` is empty — the browser
      sets the multipart boundary itself.
    """

    url: str
    method: Literal["PUT", "POST"]
    headers: dict[str, str] = field(default_factory=dict)
    fields: dict[str, str] = field(default_factory=dict)


class FileStorageProvider(ABC):
    """Object-storage operations needed by the file upload/download surfaces."""

    @abstractmethod
    def make_upload_target(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,
        ttl_seconds: int,
    ) -> UploadTarget:
        """Presigned target for a browser-direct upload."""

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
    def upload_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
    ) -> None:
        """Server-side write for proxied uploads (no presigned round-trip)."""

    @abstractmethod
    def upload_stream(
        self,
        *,
        bucket: str,
        object_name: str,
        fileobj: BinaryIO,
        content_type: str,
        max_bytes: int,
    ) -> None:
        """Stream a file object to storage in bounded chunks.

        Never materializes the whole source in memory — for a large upload
        (long-session audio) that is the difference between flat and
        hundreds-of-MB heap. Enforces ``max_bytes``, raising
        :class:`FileTooLargeError` when the source is larger.
        """

    @abstractmethod
    def delete(self, *, bucket: str, object_name: str) -> None:
        """Delete an object; already-gone is a success."""

    @abstractmethod
    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        """Return every object name in ``bucket`` starting with ``prefix``."""


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

    def make_upload_target(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,
        ttl_seconds: int,
    ) -> UploadTarget:
        from .signed_upload import make_upload_url

        url = make_upload_url(
            client=self._client(),
            bucket=bucket,
            object_name=object_name,
            content_type=content_type,
            max_bytes=max_bytes,
            ttl_seconds=ttl_seconds,
        )
        # Mirrors what the URL was signed against — GCS rejects a PUT
        # whose headers don't match the signature.
        return UploadTarget(
            url=url,
            method="PUT",
            headers={
                "Content-Type": content_type,
                "x-goog-content-length-range": f"0,{max_bytes}",
            },
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

    def upload_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
    ) -> None:
        blob = self._client().bucket(bucket).blob(object_name)
        blob.upload_from_string(data, content_type=content_type)

    def upload_stream(
        self,
        *,
        bucket: str,
        object_name: str,
        fileobj: BinaryIO,
        content_type: str,
        max_bytes: int,
    ) -> None:
        blob = self._client().bucket(bucket).blob(object_name)
        # A resumable upload streams the source in ``chunk_size`` pieces
        # instead of buffering it whole, so heap stays flat regardless of
        # file size. The capped reader rejects an oversized stream mid-flight.
        blob.chunk_size = _STREAM_CHUNK_SIZE
        blob.upload_from_file(
            _CappedReader(fileobj, max_bytes),
            content_type=content_type,
        )

    def delete(self, *, bucket: str, object_name: str) -> None:
        from .signed_upload import delete_blob

        delete_blob(client=self._client(), bucket=bucket, object_name=object_name)

    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        from .signed_upload import list_blob_names

        return list_blob_names(client=self._client(), bucket=bucket, prefix=prefix)


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

    def make_upload_target(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,
        ttl_seconds: int,
    ) -> UploadTarget:
        # Presigned POST rather than PUT: only POST policies can carry a
        # content-length-range condition, which is what enforces
        # max_bytes at S3 (parity with GCS's signed size-range header).
        post = self._client().generate_presigned_post(
            Bucket=bucket,
            Key=object_name,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 0, max_bytes],
            ],
            ExpiresIn=ttl_seconds,
        )
        return UploadTarget(url=post["url"], method="POST", fields=dict(post["fields"]))

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

    def upload_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
    ) -> None:
        self._client().put_object(
            Bucket=bucket,
            Key=object_name,
            Body=data,
            ContentType=content_type,
        )

    def upload_stream(
        self,
        *,
        bucket: str,
        object_name: str,
        fileobj: BinaryIO,
        content_type: str,
        max_bytes: int,
    ) -> None:
        # ``upload_fileobj`` runs a managed multipart transfer that pulls the
        # source in parts via ``read`` — the capped reader bounds it and
        # rejects an oversized stream.
        self._client().upload_fileobj(
            _CappedReader(fileobj, max_bytes),
            bucket,
            object_name,
            ExtraArgs={"ContentType": content_type},
        )

    def delete(self, *, bucket: str, object_name: str) -> None:
        # S3 DeleteObject is already idempotent — deleting a missing key
        # returns 204, matching the GCS backend's swallow-NotFound behavior.
        self._client().delete_object(Bucket=bucket, Key=object_name)

    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        client = self._client()
        names: list[str] = []
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            names.extend(obj["Key"] for obj in page.get("Contents", []))
        return names


class LocalFileStorage(FileStorageProvider):
    """Local-filesystem backend (self-hosted deployments; e.g. an EFS mount).

    ``bucket`` is an absolute base directory and ``object_name`` a relative
    path beneath it. Only the server-side byte ops are supported: minting
    browser-direct URLs requires a cloud signing service, and the surfaces
    that need those (patient documents, session audio) are configured with
    a cloud provider via ``file_storage_provider``.
    """

    def make_upload_target(
        self,
        *,
        bucket: str,
        object_name: str,
        content_type: str,
        max_bytes: int,
        ttl_seconds: int,
    ) -> UploadTarget:
        raise NotImplementedError("local file storage cannot mint browser-direct upload URLs")

    def make_download_url(
        self,
        *,
        bucket: str,
        object_name: str,
        ttl_seconds: int,
        response_disposition: str | None = None,
    ) -> str:
        raise NotImplementedError("local file storage cannot mint browser-direct download URLs")

    def fetch_metadata(
        self,
        *,
        bucket: str,
        object_name: str,
    ) -> tuple[int, str | None] | None:
        path = Path(bucket) / object_name
        if not path.is_file():
            return None
        # The local filesystem keeps no content-type metadata.
        return path.stat().st_size, None

    def download_bytes(self, *, bucket: str, object_name: str) -> bytes:
        return (Path(bucket) / object_name).read_bytes()

    def upload_bytes(
        self,
        *,
        bucket: str,
        object_name: str,
        data: bytes,
        content_type: str,
    ) -> None:
        _ = content_type  # no content-type metadata on a plain filesystem
        dest = Path(bucket) / object_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def upload_stream(
        self,
        *,
        bucket: str,
        object_name: str,
        fileobj: BinaryIO,
        content_type: str,
        max_bytes: int,
    ) -> None:
        _ = content_type  # no content-type metadata on a plain filesystem
        dest = Path(bucket) / object_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with dest.open("wb") as out:
            while True:
                chunk = fileobj.read(_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    # Leave no partial file behind on rejection.
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise FileTooLargeError(max_bytes)
                out.write(chunk)

    def delete(self, *, bucket: str, object_name: str) -> None:
        (Path(bucket) / object_name).unlink(missing_ok=True)

    def list_names(self, *, bucket: str, prefix: str) -> list[str]:
        base = Path(bucket)
        prefix_path = base / prefix
        parent = prefix_path.parent
        if not parent.is_dir():
            return []
        stem = prefix_path.name
        return [
            str((parent / entry.name).relative_to(base))
            for entry in parent.iterdir()
            if entry.is_file() and entry.name.startswith(stem)
        ]


def file_storage_from_settings(settings: Settings) -> FileStorageProvider:
    """Construct the configured provider. GCS is the default."""
    if settings.file_storage_provider == "s3":
        return S3FileStorage(
            region=settings.aws_region,
            endpoint_url=settings.aws_s3_endpoint_url,
        )
    return GcsFileStorage()
