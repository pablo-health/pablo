# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where the GCS provider sends its API calls and signs its URLs for.

The two endpoint settings exist for a local stand-in; a managed deployment
leaves both unset and must keep signing for storage.googleapis.com, since
that is the host the browser is handed and the one the page's
Content-Security-Policy allows. These run the real client and the real
signer, self-signing with a throwaway key, so no network is touched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from app.services.file_storage import GcsFileStorage, file_storage_from_settings
from app.settings import Settings

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fake_gcs_signing

# Never dialled: signing is local and nothing here makes an API call.
_UNREACHABLE_TOKEN_URI = "http://127.0.0.1:1/token"


@pytest.fixture(autouse=True)
def service_account_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_gcs_signing.mint_credentials(tmp_path, token_uri=_UNREACHABLE_TOKEN_URI)
    monkeypatch.setenv(
        "GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / fake_gcs_signing.CREDENTIALS_FILE)
    )
    monkeypatch.delenv("STORAGE_EMULATOR_HOST", raising=False)
    monkeypatch.delenv("API_ENDPOINT_OVERRIDE", raising=False)


def _storage(**overrides: Any) -> GcsFileStorage:
    storage = file_storage_from_settings(
        Settings(database_url="postgresql://test:test@localhost:5432/test", **overrides)
    )
    assert isinstance(storage, GcsFileStorage)
    return storage


def _signed_hosts(storage: GcsFileStorage) -> set[str]:
    upload = storage.make_upload_target(
        bucket="docs",
        object_name="t/doc",
        content_type="application/pdf",
        max_bytes=10,
        ttl_seconds=60,
    )
    download = storage.make_download_url(
        bucket="docs", object_name="t/doc", ttl_seconds=60, response_disposition="attachment"
    )
    return {urlparse(upload.url).netloc, urlparse(download).netloc}


def test_defaults_sign_for_google_and_call_googles_api() -> None:
    storage = _storage()

    assert storage._client().api_endpoint == "https://storage.googleapis.com"
    assert _signed_hosts(storage) == {"storage.googleapis.com"}


def test_configured_endpoints_are_used_each_for_its_own_purpose() -> None:
    storage = _storage(
        gcs_api_endpoint="http://storage-api.test:9000",
        gcs_signed_url_endpoint="http://localhost:9001",
    )

    assert storage._client().api_endpoint == "http://storage-api.test:9000"
    assert _signed_hosts(storage) == {"localhost:9001"}
