# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for ComplianceStorageBackend's URI↔provider routing.

The backend is a thin mapper over the three FileStorageProvider
implementations — these tests pin the scheme routing, the opaque-URI
round trip for each root shape, and the unconfigured error path. The
providers' own behavior is covered by test_file_storage.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from app.services.compliance_storage import (
    ComplianceStorageBackend,
    ComplianceStorageNotConfiguredError,
)

if TYPE_CHECKING:
    from pathlib import Path


class _FakeBlob:
    def __init__(self) -> None:
        self.data = b""
        self.content_type: str | None = None

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self.data = data
        self.content_type = content_type

    def download_as_bytes(self) -> bytes:
        return self.data

    def delete(self) -> None:
        self.data = b""


class _FakeGcsClient:
    def __init__(self) -> None:
        self.blobs: dict[str, _FakeBlob] = {}

    def bucket(self, name: str) -> _FakeGcsClient:
        self._bucket = name
        return self

    def blob(self, object_name: str) -> _FakeBlob:
        return self.blobs.setdefault(f"{self._bucket}/{object_name}", _FakeBlob())


class TestUnconfigured:
    def test_put_raises_clear_configuration_error(self) -> None:
        backend = ComplianceStorageBackend(None)
        with pytest.raises(ComplianceStorageNotConfiguredError):
            backend.put("tenant/x.pdf", b"data", "application/pdf")


class TestGcsRoot:
    def test_put_get_delete_round_trip(self) -> None:
        client = _FakeGcsClient()
        backend = ComplianceStorageBackend(
            "gs://compliance-bucket", gcs_client_factory=lambda: client
        )
        uri = backend.put("tenant/lic.pdf", b"pdf bytes", "application/pdf")
        assert uri == "gs://compliance-bucket/tenant/lic.pdf"
        assert b"".join(backend.get_stream(uri)) == b"pdf bytes"
        backend.delete(uri)
        assert client.blobs["compliance-bucket/tenant/lic.pdf"].data == b""

    def test_root_prefix_is_prepended_to_object_key(self) -> None:
        client = _FakeGcsClient()
        backend = ComplianceStorageBackend(
            "gs://bucket/some/prefix", gcs_client_factory=lambda: client
        )
        uri = backend.put("tenant/lic.pdf", b"x", "application/pdf")
        assert uri == "gs://bucket/some/prefix/tenant/lic.pdf"


class TestS3Root:
    def test_put_get_delete_round_trip(self) -> None:
        client = MagicMock()
        client.get_object.return_value = {"Body": MagicMock(read=lambda: b"pdf bytes")}
        backend = ComplianceStorageBackend(
            "s3://compliance-bucket", s3_client_factory=lambda: client
        )

        uri = backend.put("tenant/lic.pdf", b"pdf bytes", "application/pdf")
        assert uri == "s3://compliance-bucket/tenant/lic.pdf"
        client.put_object.assert_called_once_with(
            Bucket="compliance-bucket",
            Key="tenant/lic.pdf",
            Body=b"pdf bytes",
            ContentType="application/pdf",
        )

        assert b"".join(backend.get_stream(uri)) == b"pdf bytes"
        client.get_object.assert_called_once_with(Bucket="compliance-bucket", Key="tenant/lic.pdf")

        backend.delete(uri)
        client.delete_object.assert_called_once_with(
            Bucket="compliance-bucket", Key="tenant/lic.pdf"
        )

    def test_root_prefix_is_prepended_to_object_key(self) -> None:
        client = MagicMock()
        backend = ComplianceStorageBackend(
            "s3://bucket/some/prefix", s3_client_factory=lambda: client
        )
        uri = backend.put("tenant/lic.pdf", b"x", "application/pdf")
        assert uri == "s3://bucket/some/prefix/tenant/lic.pdf"
        assert client.put_object.call_args.kwargs["Key"] == "some/prefix/tenant/lic.pdf"


class TestLocalRoot:
    def test_put_get_delete_round_trip(self, tmp_path: Path) -> None:
        backend = ComplianceStorageBackend(str(tmp_path))
        uri = backend.put("tenant/lic.pdf", b"pdf bytes", "application/pdf")
        assert uri == f"file://{tmp_path / 'tenant' / 'lic.pdf'}"
        assert (tmp_path / "tenant" / "lic.pdf").read_bytes() == b"pdf bytes"
        assert b"".join(backend.get_stream(uri)) == b"pdf bytes"
        backend.delete(uri)
        assert not (tmp_path / "tenant" / "lic.pdf").exists()

    def test_get_stream_missing_file_raises(self, tmp_path: Path) -> None:
        backend = ComplianceStorageBackend(str(tmp_path))
        with pytest.raises(FileNotFoundError):
            list(backend.get_stream(f"file://{tmp_path}/nope.pdf"))

    def test_delete_is_idempotent(self, tmp_path: Path) -> None:
        backend = ComplianceStorageBackend(str(tmp_path))
        backend.delete(f"file://{tmp_path}/already-gone.pdf")  # no raise


class TestUnknownScheme:
    def test_get_stream_rejects_unknown_scheme(self) -> None:
        backend = ComplianceStorageBackend("gs://bucket")
        with pytest.raises(ValueError, match="unsupported storage_uri scheme"):
            list(backend.get_stream("ftp://weird/uri"))

    def test_delete_logs_and_ignores_unknown_scheme(self) -> None:
        backend = ComplianceStorageBackend("gs://bucket")
        backend.delete("ftp://weird/uri")  # no raise
