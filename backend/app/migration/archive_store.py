# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where an uploaded export lives between upload, preview and apply.

The reader and the applier read an unpacked directory on local disk. On a
single-instance deployment that directory is the whole story. On a deployment
with more than one backend instance it is not: the instance that took the
upload is not the one that runs the preview, nor the one that applies, and
the directory it unpacked would be gone from the others. So the store keeps
the archive in two places:

* **the local cache**, ``import_archive_dir``: the unpacked tree, per
  process, where the reader works; and
* **the object store**, ``import_archive_storage_root``, when one is set: one
  plain zip per run, which any instance can fetch and unpack into its own
  cache. The root is a ``gs://bucket/prefix``, ``s3://bucket/prefix`` or a
  local directory, resolved through the same provider every other file in
  the product goes through.

The object is written unlocked: an export's password opens the upload once
and is kept nowhere, so what is stored never needs it again. Deleting a run's
archive deletes both copies. The reference stored on the run row is a name,
never a path or a URL.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from ..services.file_storage import (
    FileStorageProvider,
    GcsFileStorage,
    LocalFileStorage,
    S3FileStorage,
)
from ..settings import get_settings
from .ledger import MAX_ARCHIVE_BYTES, extract_zip, practice_archive_dir, practice_archive_key

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..settings import Settings

logger = logging.getLogger(__name__)

_BUCKET_SCHEMES = ("gs://", "s3://")


def _ref_ok(ref: str) -> bool:
    return bool(ref) and "/" not in ref and ".." not in ref


class ArchiveStore:
    """One run's archive: put once, fetch on any instance, delete everywhere."""

    def __init__(
        self,
        local_base: Path,
        storage_root: str | None = None,
        *,
        provider_factory: Callable[[str], FileStorageProvider] | None = None,
    ) -> None:
        self._local_base = local_base
        self._root = (storage_root or "").strip() or None
        self._provider_factory = provider_factory or _default_provider

    # ----------------------------------------------------------------- where

    @property
    def remote(self) -> bool:
        """Whether a copy is kept beyond this instance's disk."""
        return self._root is not None

    def _bucket_and_name(self, practice_key: str, ref: str) -> tuple[str, str, str]:
        """``(scheme, bucket, object name)`` for a run's zip under the root."""
        root = self._root or ""
        digest = practice_archive_key(practice_key)
        for scheme in _BUCKET_SCHEMES:
            if root.startswith(scheme):
                bucket, _, prefix = root[len(scheme) :].partition("/")
                prefix = prefix.strip("/")
                name = f"{prefix}/{digest}/{ref}.zip" if prefix else f"{digest}/{ref}.zip"
                return scheme.removesuffix("://"), bucket, name
        return "file", root, f"{digest}/{ref}.zip"

    def _provider(self, scheme: str) -> FileStorageProvider:
        return self._provider_factory(scheme)

    # ------------------------------------------------------------------- put

    def put(self, practice_key: str, archive: Path | bytes, password: str | None = None) -> str:
        """Unpack an upload into the local cache, keep a plain copy in the object
        store when one is configured, and return the run's reference."""
        root = practice_archive_dir(self._local_base, practice_key)
        root.mkdir(parents=True, exist_ok=True)
        ref = uuid.uuid4().hex
        target = root / ref
        target.mkdir()
        try:
            extract_zip(archive, target, password)
            if self.remote:
                self._upload(practice_key, ref, target)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return ref

    def _upload(self, practice_key: str, ref: str, unpacked: Path) -> None:
        """A plain zip of the unpacked tree, streamed up from a temp file."""
        scheme, bucket, name = self._bucket_and_name(practice_key, ref)
        with tempfile.TemporaryFile() as spool:
            with zipfile.ZipFile(spool, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file in sorted(p for p in unpacked.rglob("*") if p.is_file()):
                    zf.write(file, file.relative_to(unpacked).as_posix())
            spool.seek(0)
            self._provider(scheme).upload_stream(
                bucket=bucket,
                object_name=name,
                fileobj=spool,
                content_type="application/zip",
                max_bytes=MAX_ARCHIVE_BYTES,
            )

    # ------------------------------------------------------------------ path

    def path(self, practice_key: str, ref: str) -> Path | None:
        """The unpacked archive on this instance, fetched from the object store
        when this instance has not seen it; ``None`` when it is gone."""
        if not _ref_ok(ref):
            return None
        local = practice_archive_dir(self._local_base, practice_key) / ref
        if local.is_dir():
            return local
        if not self.remote:
            return None
        scheme, bucket, name = self._bucket_and_name(practice_key, ref)
        try:
            data = self._provider(scheme).download_bytes(bucket=bucket, object_name=name)
        except Exception:  # not there, or the store is unreachable: both read as gone
            logger.info("import archive not fetched from the object store", exc_info=True)
            return None
        local.parent.mkdir(parents=True, exist_ok=True)
        local.mkdir(exist_ok=True)
        try:
            extract_zip(data, local)
        except Exception:
            shutil.rmtree(local, ignore_errors=True)
            raise
        return local

    # ---------------------------------------------------------------- delete

    def delete(self, practice_key: str, ref: str | None) -> bool:
        """Remove the local copy and the stored one. True when anything was there."""
        if not ref or not _ref_ok(ref):
            return False
        local = practice_archive_dir(self._local_base, practice_key) / ref
        existed = local.is_dir()
        shutil.rmtree(local, ignore_errors=True)
        if self.remote:
            scheme, bucket, name = self._bucket_and_name(practice_key, ref)
            provider = self._provider(scheme)
            try:
                if name in provider.list_names(bucket=bucket, prefix=name):
                    existed = True
                    provider.delete(bucket=bucket, object_name=name)
            except Exception:  # unreachable: the sweep comes back for it
                logger.info("import archive object not deleted", exc_info=True)
        return existed


def _default_provider(scheme: str) -> FileStorageProvider:
    settings = get_settings()
    if scheme == "gs":
        return GcsFileStorage(
            api_endpoint=settings.gcs_api_endpoint,
            signed_url_endpoint=settings.gcs_signed_url_endpoint,
        )
    if scheme == "s3":
        return S3FileStorage(region=settings.aws_region, endpoint_url=settings.aws_s3_endpoint_url)
    return LocalFileStorage()


def archive_store_from_settings(settings: Settings) -> ArchiveStore:
    """The store the deployment configured: the local directory, plus the object
    root when one is set."""
    return ArchiveStore(Path(settings.import_archive_dir), settings.import_archive_storage_root)
