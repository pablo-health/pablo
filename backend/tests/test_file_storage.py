# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the pluggable file-storage providers.

The GCS backend is exercised against the same in-memory fake client used
by test_patient_documents_service.py; the S3 backend presigns offline
against a real boto3 client with dummy credentials (presigning is pure
local computation — no network).
"""

from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import boto3
import pytest
from app.services.file_storage import (
    GcsFileStorage,
    S3FileStorage,
    file_storage_from_settings,
)
from app.settings import Settings
from botocore.config import Config
from botocore.exceptions import ClientError
from google.cloud.exceptions import NotFound

# ---- GCS fakes (mirrors the pattern in test_patient_documents_service) ----


class _FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self._data = b""
        self.size: int | None = None
        self.content_type: str | None = None

    def reload(self) -> None:
        if self.size is None:
            raise NotFound("blob not found")

    def upload_from_string(self, data: bytes, content_type: str | None = None) -> None:
        self._data = data
        self.size = len(data)
        self.content_type = content_type

    def download_as_bytes(self) -> bytes:
        return self._data

    def delete(self) -> None:
        if self.size is None:
            raise NotFound("blob not found")
        self._data = b""
        self.size = None

    def generate_signed_url(self, **kwargs: Any) -> str:
        return f"https://fake.googleusercontent.example/{self.name}?sig=xyz"


class _FakeBucket:
    def __init__(self, name: str) -> None:
        self._blobs: dict[str, _FakeBlob] = {}

    def blob(self, object_name: str) -> _FakeBlob:
        return self._blobs.setdefault(object_name, _FakeBlob(object_name))


class _FakeGcsClient:
    def __init__(self) -> None:
        self._buckets: dict[str, _FakeBucket] = {}

    def bucket(self, name: str) -> _FakeBucket:
        return self._buckets.setdefault(name, _FakeBucket(name))


@pytest.fixture
def fake_gcs() -> _FakeGcsClient:
    return _FakeGcsClient()


@pytest.fixture
def gcs_storage(fake_gcs: _FakeGcsClient) -> GcsFileStorage:
    return GcsFileStorage(client_factory=lambda: fake_gcs)


class TestGcsFileStorage:
    def test_upload_target_is_bare_put_and_download_delegates(
        self, gcs_storage: GcsFileStorage
    ) -> None:
        up = gcs_storage.make_upload_target(
            bucket="b",
            object_name="t/doc-1",
            content_type="application/pdf",
            max_bytes=100,
            ttl_seconds=300,
        )
        down = gcs_storage.make_download_url(bucket="b", object_name="t/doc-1", ttl_seconds=300)
        assert "t/doc-1" in up.url
        assert up.method == "PUT"
        # The headers mirror what the URL was signed against; the client
        # attaches them verbatim.
        assert up.headers == {
            "Content-Type": "application/pdf",
            "x-goog-content-length-range": "0,100",
        }
        assert up.fields == {}
        assert "t/doc-1" in down

    def test_fetch_metadata_missing_returns_none(self, gcs_storage: GcsFileStorage) -> None:
        assert gcs_storage.fetch_metadata(bucket="b", object_name="nope") is None

    def test_fetch_metadata_and_download_round_trip(
        self, gcs_storage: GcsFileStorage, fake_gcs: _FakeGcsClient
    ) -> None:
        fake_gcs.bucket("b").blob("t/doc-1").upload_from_string(
            b"%PDF-1.7 body", content_type="application/pdf"
        )
        assert gcs_storage.fetch_metadata(bucket="b", object_name="t/doc-1") == (
            13,
            "application/pdf",
        )
        assert gcs_storage.download_bytes(bucket="b", object_name="t/doc-1") == b"%PDF-1.7 body"

    def test_delete_is_idempotent_on_missing(
        self, gcs_storage: GcsFileStorage, fake_gcs: _FakeGcsClient
    ) -> None:
        fake_gcs.bucket("b").blob("t/doc-1").upload_from_string(b"x", content_type="text/plain")
        gcs_storage.delete(bucket="b", object_name="t/doc-1")
        # second delete hits a NotFound inside the SDK — must not raise
        gcs_storage.delete(bucket="b", object_name="t/doc-1")
        assert gcs_storage.fetch_metadata(bucket="b", object_name="t/doc-1") is None


# ---- S3 ---------------------------------------------------------------


def _offline_s3_client() -> Any:
    return boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="testing",  # dummy creds: presigning is offline
        aws_secret_access_key="testing",  # noqa: S106 — dummy test credential
        config=Config(signature_version="s3v4"),
    )


@pytest.fixture
def s3_storage() -> S3FileStorage:
    return S3FileStorage(client_factory=_offline_s3_client)


class TestS3FileStorage:
    def test_upload_target_is_presigned_post_with_size_and_type_policy(
        self, s3_storage: S3FileStorage
    ) -> None:
        target = s3_storage.make_upload_target(
            bucket="pablo-docs",
            object_name="tenant-A/chart/doc-1",
            content_type="application/pdf",
            max_bytes=100,
            ttl_seconds=300,
        )
        assert target.method == "POST"
        assert target.headers == {}
        assert "pablo-docs" in target.url
        assert target.fields["key"] == "tenant-A/chart/doc-1"
        assert target.fields["Content-Type"] == "application/pdf"
        assert "x-amz-signature" in target.fields
        # The signed policy document is what S3 enforces at upload time —
        # it must carry both the content-type match and the size range.
        policy = json.loads(base64.b64decode(target.fields["policy"]))
        assert ["content-length-range", 0, 100] in policy["conditions"]
        assert {"Content-Type": "application/pdf"} in policy["conditions"]

    def test_download_url_carries_response_disposition(self, s3_storage: S3FileStorage) -> None:
        url = s3_storage.make_download_url(
            bucket="pablo-docs",
            object_name="tenant-A/chart/doc-1",
            ttl_seconds=300,
            response_disposition='attachment; filename="report.pdf"',
        )
        query = parse_qs(urlparse(url).query)
        assert query["response-content-disposition"] == ['attachment; filename="report.pdf"']

    def test_fetch_metadata_returns_size_and_content_type(self) -> None:
        client = MagicMock()
        client.head_object.return_value = {
            "ContentLength": 42,
            "ContentType": "application/pdf",
        }
        storage = S3FileStorage(client_factory=lambda: client)
        assert storage.fetch_metadata(bucket="b", object_name="k") == (42, "application/pdf")
        client.head_object.assert_called_once_with(Bucket="b", Key="k")

    def test_fetch_metadata_missing_returns_none(self) -> None:
        client = MagicMock()
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        storage = S3FileStorage(client_factory=lambda: client)
        assert storage.fetch_metadata(bucket="b", object_name="k") is None

    def test_fetch_metadata_propagates_non_404_errors(self) -> None:
        client = MagicMock()
        client.head_object.side_effect = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
        )
        storage = S3FileStorage(client_factory=lambda: client)
        with pytest.raises(ClientError):
            storage.fetch_metadata(bucket="b", object_name="k")

    def test_download_bytes_reads_body(self) -> None:
        client = MagicMock()
        client.get_object.return_value = {"Body": MagicMock(read=lambda: b"pdf bytes")}
        storage = S3FileStorage(client_factory=lambda: client)
        assert storage.download_bytes(bucket="b", object_name="k") == b"pdf bytes"

    def test_delete_calls_delete_object(self) -> None:
        client = MagicMock()
        storage = S3FileStorage(client_factory=lambda: client)
        storage.delete(bucket="b", object_name="k")
        client.delete_object.assert_called_once_with(Bucket="b", Key="k")


# ---- settings factory ---------------------------------------------------


def _settings(**overrides: Any) -> Settings:
    return Settings(
        database_url="postgresql://test:test@localhost:5432/test",
        **overrides,
    )


class TestFileStorageFromSettings:
    def test_defaults_to_gcs(self) -> None:
        assert isinstance(file_storage_from_settings(_settings()), GcsFileStorage)

    def test_s3_provider_plumbs_region_and_endpoint(self) -> None:
        storage = file_storage_from_settings(
            _settings(
                file_storage_provider="s3",
                aws_region="us-west-2",
                aws_s3_endpoint_url="http://localhost:9000",
            )
        )
        assert isinstance(storage, S3FileStorage)
        assert storage._region == "us-west-2"
        assert storage._endpoint_url == "http://localhost:9000"
