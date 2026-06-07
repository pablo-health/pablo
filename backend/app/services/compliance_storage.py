# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Storage backend for compliance documents.

Compliance documents (license copies, insurance declarations, etc.) are the
clinician's own credentials — not patient PHI. They are uploaded via a
server-side multipart route (the server proxies the bytes to storage) rather
than the browser-direct signed-URL flow used for patient records.

``storage_uri`` is intentionally opaque:

* ``gs://<bucket>/<object>`` — Google Cloud Storage (managed deployments).
* ``file:///abs/path/to/file`` — local filesystem (self-hosted deployments
  that don't have a cloud storage bucket).

Switching backends is a configuration change (``COMPLIANCE_DOCUMENTS_URI``),
not a code change. No other module should parse the URI scheme — all
storage operations go through :class:`ComplianceStorageBackend`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

# File-URI scheme prefix used by the local-filesystem backend.
_FILE_SCHEME = "file://"
_GS_SCHEME = "gs://"


class ComplianceStorageNotConfiguredError(Exception):
    """Raised when no storage root has been configured.

    Surface returns 503 so operators without a bucket or local directory
    get a clear configuration message instead of a traceback.
    """


class ComplianceStorageBackend:
    """Opaque read/write interface over GCS or the local filesystem.

    Callers obtain a URI from :meth:`put` and pass it back to
    :meth:`get_stream` or :meth:`delete`. The URI scheme determines
    which concrete path is used; the caller never inspects the scheme.

    Parameters
    ----------
    storage_root:
        Either a ``gs://<bucket>`` prefix (GCS) or an absolute local
        directory path (local filesystem). ``None`` means the feature
        is not configured — any write or read raises
        :class:`ComplianceStorageNotConfiguredError`.
    gcs_client_factory:
        Optional callable that returns a ``google.cloud.storage.Client``.
        Injected by tests; production code creates a real client lazily.
    """

    def __init__(
        self,
        storage_root: str | None,
        *,
        gcs_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._root = storage_root
        self._gcs_client_factory = gcs_client_factory

    # --- internal helpers ------------------------------------------------

    def _require_root(self) -> str:
        if not self._root:
            raise ComplianceStorageNotConfiguredError(
                "compliance_documents_storage_root is not configured"
            )
        return self._root

    def _gcs_client(self) -> Any:
        if self._gcs_client_factory is not None:
            return self._gcs_client_factory()
        from google.cloud import storage  # type: ignore[attr-defined]

        return storage.Client()

    def _is_gcs_root(self, root: str) -> bool:
        return root.startswith(_GS_SCHEME)

    def _parse_gs_uri(self, uri: str) -> tuple[str, str]:
        """Return (bucket, object_name) from a gs:// URI."""
        without_scheme = uri[len(_GS_SCHEME) :]
        bucket, _, obj = without_scheme.partition("/")
        return bucket, obj

    def _parse_file_uri(self, uri: str) -> Path:
        return Path(uri[len(_FILE_SCHEME) :])

    # --- public API ------------------------------------------------------

    def put(self, object_key: str, data: bytes, mime_type: str) -> str:
        """Store ``data`` under ``object_key`` and return the opaque URI.

        ``object_key`` must be a relative path-like string (e.g.
        ``<tenant>/<uuid>.pdf``). The backend prepends its configured
        root so the returned URI is always absolute.
        """
        root = self._require_root()
        if self._is_gcs_root(root):
            # root is "gs://bucket" or "gs://bucket/prefix"; normalise
            without_scheme = root[len(_GS_SCHEME) :]
            if "/" in without_scheme:
                bucket_name, prefix_slash = without_scheme.split("/", 1)
                prefix = prefix_slash.rstrip("/")
                full_key = f"{prefix}/{object_key}" if prefix else object_key
            else:
                bucket_name = without_scheme
                full_key = object_key
            client = self._gcs_client()
            blob = client.bucket(bucket_name).blob(full_key)
            blob.upload_from_string(data, content_type=mime_type)
            uri = f"{_GS_SCHEME}{bucket_name}/{full_key}"
        else:
            # Local filesystem
            base = Path(root)
            dest = base / object_key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            uri = f"{_FILE_SCHEME}{dest}"
        return uri

    def get_stream(self, uri: str) -> Iterator[bytes]:
        """Yield the bytes stored at ``uri`` in chunks.

        The iterator is a generator so callers that stream to HTTP responses
        don't buffer the full file. Raises ``FileNotFoundError`` when the
        object does not exist.
        """
        if uri.startswith(_GS_SCHEME):
            bucket_name, obj = self._parse_gs_uri(uri)
            client = self._gcs_client()
            blob = client.bucket(bucket_name).blob(obj)
            data: bytes = blob.download_as_bytes()
            yield data
        elif uri.startswith(_FILE_SCHEME):
            path = self._parse_file_uri(uri)
            if not path.exists():
                raise FileNotFoundError(f"file not found: {path}")
            chunk_size = 64 * 1024
            with path.open("rb") as fh:
                while chunk := fh.read(chunk_size):
                    yield chunk
        else:
            raise ValueError(f"unsupported storage_uri scheme: {uri!r}")

    def delete(self, uri: str) -> None:
        """Best-effort delete; logs a warning if the object is already gone."""
        if uri.startswith(_GS_SCHEME):
            from google.cloud.exceptions import NotFound  # type: ignore[attr-defined]

            bucket_name, obj = self._parse_gs_uri(uri)
            client = self._gcs_client()
            blob = client.bucket(bucket_name).blob(obj)
            try:
                blob.delete()
            except NotFound:
                logger.warning("compliance document already gone: %s", uri)
        elif uri.startswith(_FILE_SCHEME):
            path = self._parse_file_uri(uri)
            try:
                path.unlink()
            except FileNotFoundError:
                logger.warning("compliance document already gone: %s", uri)
        else:
            logger.warning("unsupported storage_uri scheme on delete: %s", uri)
