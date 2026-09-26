# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The end-to-end harness's fake GCS server, held to what Google's signer emits.

``scripts/e2e/fake_gcs.py`` is never imported by the app, but every document
spec in the compose lane passes or fails on whether it checks a V4 signature
the way GCS does. A fake that drifted from Google — accepting a tampered URL,
or refusing a good one — would make that lane green for the wrong reason or
red for no reason, and nothing else would notice.

So nothing here is hand-built. The server runs for real on a local port, and
every URL and request is made by the production path: ``GcsFileStorage`` over
the real ``google-cloud-storage`` client, self-signing with a service-account
key minted the way the stack mints one, fetching its token from the fake.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest
import uvicorn
from app.services.file_storage import GcsFileStorage, UploadTarget

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "e2e"))
import fake_gcs
import fake_gcs_signing

if TYPE_CHECKING:
    from collections.abc import Iterator

_BUCKET = "pablo-e2e-documents"
_ORIGIN = "http://localhost:3000"


class _Clock:
    """The fake's notion of now, which a test can move forward."""

    def __init__(self) -> None:
        self.offset = timedelta()

    def __call__(self) -> datetime:
        return datetime.now(tz=UTC) + self.offset


@pytest.fixture(scope="module")
def clock() -> _Clock:
    return _Clock()


@pytest.fixture(scope="module")
def endpoint(tmp_path_factory: pytest.TempPathFactory, clock: _Clock) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    credentials = tmp_path_factory.mktemp("gcs-credentials")
    fake_gcs_signing.mint_credentials(credentials, token_uri=f"{base}/token")
    app = fake_gcs.create_app(
        public_key=fake_gcs_signing.load_public_key(credentials),
        cors_origins=[_ORIGIN],
        clock=clock,
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "fake GCS server did not start"
        time.sleep(0.01)
    with pytest.MonkeyPatch.context() as env:
        env.setenv(
            "GOOGLE_APPLICATION_CREDENTIALS", str(credentials / fake_gcs_signing.CREDENTIALS_FILE)
        )
        env.delenv("STORAGE_EMULATOR_HOST", raising=False)
        yield base
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def storage(endpoint: str, clock: _Clock) -> GcsFileStorage:
    clock.offset = timedelta()
    return GcsFileStorage(api_endpoint=endpoint, signed_url_endpoint=endpoint)


def _target(storage: GcsFileStorage, name: str, *, max_bytes: int = 1024) -> UploadTarget:
    return storage.make_upload_target(
        bucket=_BUCKET,
        object_name=name,
        content_type="application/pdf",
        max_bytes=max_bytes,
        ttl_seconds=300,
    )


def _put(target: UploadTarget, body: bytes, **header_overrides: str) -> httpx.Response:
    return httpx.put(target.url, headers={**target.headers, **header_overrides}, content=body)


class TestSignedUrls:
    def test_valid_put_is_stored_with_its_signed_type(self, storage: GcsFileStorage) -> None:
        body = b"%PDF-1.7 a signed upload"
        target = _target(storage, "t1/signed-put")

        assert _put(target, body).status_code == 200
        assert storage.fetch_metadata(bucket=_BUCKET, object_name="t1/signed-put") == (
            len(body),
            "application/pdf",
        )
        assert storage.download_bytes(bucket=_BUCKET, object_name="t1/signed-put") == body

    def test_valid_get_returns_bytes_with_the_signed_disposition(
        self, storage: GcsFileStorage
    ) -> None:
        body = b"%PDF-1.7 download me"
        assert _put(_target(storage, "t1/signed-get"), body).status_code == 200
        disposition = 'attachment; filename="intake form.pdf"'

        url = storage.make_download_url(
            bucket=_BUCKET,
            object_name="t1/signed-get",
            ttl_seconds=300,
            response_disposition=disposition,
        )
        fetched = httpx.get(url)

        assert fetched.status_code == 200
        assert fetched.content == body
        assert fetched.headers["content-disposition"] == disposition

    def test_tampered_signature_is_refused(self, storage: GcsFileStorage) -> None:
        target = _target(storage, "t1/tampered")
        flipped = "0" if target.url[-1] != "0" else "1"
        tampered = UploadTarget(url=target.url[:-1] + flipped, method="PUT", headers=target.headers)

        refused = _put(tampered, b"%PDF")

        assert refused.status_code == 403
        assert b"SignatureDoesNotMatch" in refused.content
        assert storage.fetch_metadata(bucket=_BUCKET, object_name="t1/tampered") is None

    def test_a_url_signed_for_one_object_cannot_fetch_another(
        self, storage: GcsFileStorage
    ) -> None:
        assert _put(_target(storage, "t1/secret"), b"%PDF").status_code == 200
        url = storage.make_download_url(bucket=_BUCKET, object_name="t1/public", ttl_seconds=300)

        assert httpx.get(url.replace("t1/public", "t1/secret")).status_code == 403

    def test_unsigned_request_is_refused(self, endpoint: str) -> None:
        refused = httpx.put(f"{endpoint}/{_BUCKET}/t1/anonymous", content=b"%PDF")

        assert refused.status_code == 403
        assert b"AccessDenied" in refused.content

    def test_expired_url_is_refused(self, storage: GcsFileStorage, clock: _Clock) -> None:
        target = _target(storage, "t1/expired")
        clock.offset = timedelta(seconds=301)

        refused = _put(target, b"%PDF")

        assert refused.status_code == 400
        assert b"ExpiredToken" in refused.content

    def test_wrong_content_type_is_refused(self, storage: GcsFileStorage) -> None:
        target = _target(storage, "t1/retyped")

        refused = _put(target, b"not a pdf", **{"Content-Type": "text/plain"})

        # The type is a signed header, so a different one is a different signature.
        assert refused.status_code == 403
        assert storage.fetch_metadata(bucket=_BUCKET, object_name="t1/retyped") is None

    def test_body_over_the_signed_length_range_is_refused(self, storage: GcsFileStorage) -> None:
        refused = _put(_target(storage, "t1/oversized", max_bytes=16), b"x" * 17)

        assert refused.status_code == 400
        assert b"EntityTooLarge" in refused.content
        assert storage.fetch_metadata(bucket=_BUCKET, object_name="t1/oversized") is None

    def test_a_widened_length_range_breaks_the_signature(self, storage: GcsFileStorage) -> None:
        refused = _put(
            _target(storage, "t1/widened", max_bytes=16),
            b"x" * 17,
            **{"x-goog-content-length-range": "0,1000"},
        )

        assert refused.status_code == 403

    def test_browser_preflight_is_answered_for_the_frontend_origin(self, endpoint: str) -> None:
        preflight = httpx.options(
            f"{endpoint}/{_BUCKET}/t1/anything",
            headers={
                "Origin": _ORIGIN,
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type,x-goog-content-length-range",
            },
        )

        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == _ORIGIN


class TestJsonApi:
    def test_server_side_upload_list_head_and_delete(self, storage: GcsFileStorage) -> None:
        for name, data in (("t2/a", b"0123456789"), ("t2/b", b"b"), ("t3/c", b"c")):
            storage.upload_bytes(
                bucket=_BUCKET, object_name=name, data=data, content_type="text/plain"
            )

        assert storage.list_names(bucket=_BUCKET, prefix="t2/") == ["t2/a", "t2/b"]
        assert storage.fetch_metadata(bucket=_BUCKET, object_name="t2/a") == (10, "text/plain")
        assert storage.download_head(bucket=_BUCKET, object_name="t2/a", length=4) == b"0123"

        storage.delete(bucket=_BUCKET, object_name="t2/a")
        storage.delete(bucket=_BUCKET, object_name="t2/a")
        assert storage.fetch_metadata(bucket=_BUCKET, object_name="t2/a") is None

    def test_streamed_upload_spans_resumable_chunks(self, storage: GcsFileStorage) -> None:
        # Past the 1 MiB chunk size, so the upload takes more than one PUT.
        body = bytes(range(256)) * (10 * 1024)

        storage.upload_stream(
            bucket="pablo-audio",
            object_name="t2/session.wav",
            fileobj=BytesIO(body),
            content_type="audio/wav",
            max_bytes=len(body),
        )

        assert storage.download_bytes(bucket="pablo-audio", object_name="t2/session.wav") == body
        assert storage.fetch_metadata(bucket="pablo-audio", object_name="t2/session.wav") == (
            len(body),
            "audio/wav",
        )
