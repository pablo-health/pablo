# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Storage backend for compliance documents.

Compliance documents (license copies, insurance declarations, etc.) are the
clinician's own credentials — not patient PHI. They are uploaded via a
server-side multipart route (the server proxies the bytes to storage) rather
than the browser-direct signed-URL flow used for patient records.

``storage_uri`` is intentionally opaque:

* ``gs://<bucket>/<object>``   — Google Cloud Storage (managed deployments).
* ``s3://<bucket>/<object>``   — AWS S3 or S3-compatible stores.
* ``file:///abs/path/to/file`` — local filesystem (self-hosted deployments;
  e.g. an EFS/NFS mount).

Switching backends is a configuration change (``COMPLIANCE_DOCUMENTS_URI``),
not a code change. No other module should parse the URI scheme — all storage
operations go through :class:`ComplianceStorageBackend`, a thin URI↔object
mapper over the shared :class:`~.file_storage.FileStorageProvider`
implementations (GCS, S3, local).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .file_storage import GcsFileStorage, LocalFileStorage, S3FileStorage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from typing import Any

    from ..settings import Settings
    from .file_storage import FileStorageProvider

logger = logging.getLogger(__name__)

# URI scheme prefixes. Bucket schemes share the <bucket>/<object> shape.
_FILE_SCHEME = "file://"
_BUCKET_SCHEMES = ("gs://", "s3://")


class ComplianceStorageNotConfiguredError(Exception):
    """Raised when no storage root has been configured.

    Surface returns 503 so operators without a bucket or local directory
    get a clear configuration message instead of a traceback.
    """


class ComplianceStorageBackend:
    """Opaque read/write interface over GCS, S3, or the local filesystem.

    Callers obtain a URI from :meth:`put` and pass it back to
    :meth:`get_stream` or :meth:`delete`. The URI scheme selects the
    concrete :class:`FileStorageProvider`; the caller never inspects it.

    Parameters
    ----------
    storage_root:
        ``gs://<bucket>[/prefix]``, ``s3://<bucket>[/prefix]``, or an
        absolute local directory path (e.g. an EFS mount). ``None`` means
        the feature is not configured — any write or read raises
        :class:`ComplianceStorageNotConfiguredError`.
    settings:
        Supplies the S3 provider's region/endpoint configuration.
        Optional; boto3's default resolution applies when omitted.
    gcs_client_factory / s3_client_factory:
        Test seams for the underlying cloud clients; production code
        creates real clients lazily.
    """

    def __init__(
        self,
        storage_root: str | None,
        *,
        settings: Settings | None = None,
        gcs_client_factory: Callable[[], Any] | None = None,
        s3_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._root = storage_root
        self._settings = settings
        self._gcs_client_factory = gcs_client_factory
        self._s3_client_factory = s3_client_factory

    # --- provider routing ------------------------------------------------

    def _require_root(self) -> str:
        if not self._root:
            raise ComplianceStorageNotConfiguredError(
                "compliance_documents_storage_root is not configured"
            )
        return self._root

    def _provider(self, scheme: str) -> FileStorageProvider:
        if scheme == "gs":
            return GcsFileStorage(client_factory=self._gcs_client_factory)
        if scheme == "s3":
            return S3FileStorage(
                region=self._settings.aws_region if self._settings else None,
                endpoint_url=(self._settings.aws_s3_endpoint_url if self._settings else None),
                client_factory=self._s3_client_factory,
            )
        return LocalFileStorage()

    @staticmethod
    def _parse_bucket_uri(uri: str) -> tuple[str, str, str] | None:
        """Split ``gs://…`` / ``s3://…`` into (scheme, bucket, object).

        Returns ``None`` for anything else (file URIs, local paths).
        """
        for prefix in _BUCKET_SCHEMES:
            if uri.startswith(prefix):
                bucket, _, obj = uri[len(prefix) :].partition("/")
                return prefix.removesuffix("://"), bucket, obj
        return None

    # --- public API --------------------------------------------------------

    def put(self, object_key: str, data: bytes, mime_type: str) -> str:
        """Store ``data`` under ``object_key`` and return the opaque URI.

        ``object_key`` must be a relative path-like string (e.g.
        ``<tenant>/<uuid>.pdf``). The backend prepends its configured
        root so the returned URI is always absolute.
        """
        root = self._require_root()
        parsed = self._parse_bucket_uri(root)
        if parsed is not None:
            scheme, bucket, prefix = parsed
            prefix = prefix.strip("/")
            full_key = f"{prefix}/{object_key}" if prefix else object_key
            self._provider(scheme).upload_bytes(
                bucket=bucket,
                object_name=full_key,
                data=data,
                content_type=mime_type,
            )
            return f"{scheme}://{bucket}/{full_key}"
        # Local directory root (e.g. an EFS mount).
        self._provider("file").upload_bytes(
            bucket=root,
            object_name=object_key,
            data=data,
            content_type=mime_type,
        )
        return f"{_FILE_SCHEME}{Path(root) / object_key}"

    def get_stream(self, uri: str) -> Iterator[bytes]:
        """Yield the bytes stored at ``uri``.

        Kept as a generator so HTTP-streaming callers keep their shape;
        documents are capped at 25 MB so a single buffered chunk is fine.
        Raises ``FileNotFoundError`` (local) or the provider SDK's
        not-found error when the object does not exist.
        """
        parsed = self._parse_bucket_uri(uri)
        if parsed is not None:
            scheme, bucket, obj = parsed
            yield self._provider(scheme).download_bytes(bucket=bucket, object_name=obj)
        elif uri.startswith(_FILE_SCHEME):
            path = Path(uri[len(_FILE_SCHEME) :])
            yield self._provider("file").download_bytes(
                bucket=str(path.parent), object_name=path.name
            )
        else:
            raise ValueError(f"unsupported storage_uri scheme: {uri!r}")

    def delete(self, uri: str) -> None:
        """Idempotent delete — an already-gone object is a success."""
        parsed = self._parse_bucket_uri(uri)
        if parsed is not None:
            scheme, bucket, obj = parsed
            self._provider(scheme).delete(bucket=bucket, object_name=obj)
        elif uri.startswith(_FILE_SCHEME):
            path = Path(uri[len(_FILE_SCHEME) :])
            self._provider("file").delete(bucket=str(path.parent), object_name=path.name)
        else:
            logger.warning("unsupported storage_uri scheme on delete: %s", uri)
