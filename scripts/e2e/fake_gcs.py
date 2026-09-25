# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in Google Cloud Storage server for the end-to-end stack.

A document upload goes browser→storage direct against a V4 signed URL, so
the stack needs something that checks a real signature — and checking it
the way GCS does is the point: the backend signs with the production
``GcsFileStorage`` code and the real ``google-cloud-storage`` signer, so a
regression in either fails here rather than in a deployment. The check
itself is in ``fake_gcs_signing.py``.

Two surfaces, each only as wide as the backend uses:

* **Signed URLs** — ``PUT`` and ``GET /{bucket}/{object}``. A PUT is held to
  its signed ``Content-Type`` (a signed header, so the signature covers it)
  and to ``x-goog-content-length-range``; a GET honours
  ``response-content-disposition``. Refusals use GCS's status and XML shape.
* **JSON API** — object metadata, media download (with ``Range``), delete,
  list by prefix, and multipart and resumable upload, reached through the
  library's ``api_endpoint`` client option. Any bearer token is accepted,
  and ``POST /token`` hands one out, since the service-account key the
  backend signs with names this server as its ``token_uri``.

Buckets spring into being on first write. Objects live in memory.

Run with ``uvicorn --factory fake_gcs:app_from_env --port 9000``; the
compose stack builds it from ``scripts/e2e/fake-gcs.Dockerfile``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import google_crc32c
from fake_gcs_signing import (
    SERVICE_ACCOUNT_EMAIL,
    SignatureError,
    SignedRequest,
    load_public_key,
    verify,
)
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Callable

    from cryptography.hazmat.primitives.asymmetric import rsa

_RANGE = re.compile(r"bytes=(\d+)-(\d*)$")
_CONTENT_RANGE = re.compile(r"bytes (?:(\d+)-(\d+)|\*)/(\d+|\*)$")
_OCTET_STREAM = "application/octet-stream"


@dataclass
class StoredObject:
    data: bytes
    content_type: str
    generation: int
    updated: datetime

    def checksums(self) -> tuple[str, str]:
        """``(crc32c, md5)``, base64 as GCS reports them."""
        crc = google_crc32c.Checksum(self.data).digest()
        md5 = hashlib.md5(self.data, usedforsecurity=False).digest()
        return base64.b64encode(crc).decode(), base64.b64encode(md5).decode()


@dataclass
class ResumableSession:
    bucket: str
    name: str
    content_type: str
    data: bytearray = field(default_factory=bytearray)


class Store:
    """Every bucket's objects, and the resumable uploads still in flight."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._buckets: dict[str, dict[str, StoredObject]] = {}
        self.sessions: dict[str, ResumableSession] = {}

    def put(self, bucket: str, name: str, data: bytes, content_type: str) -> StoredObject:
        objects = self._buckets.setdefault(bucket, {})
        previous = objects.get(name)
        obj = StoredObject(
            data=data,
            content_type=content_type,
            generation=(previous.generation + 1) if previous else 1,
            updated=self._clock(),
        )
        objects[name] = obj
        return obj

    def get(self, bucket: str, name: str) -> StoredObject | None:
        return self._buckets.get(bucket, {}).get(name)

    def delete(self, bucket: str, name: str) -> bool:
        return self._buckets.get(bucket, {}).pop(name, None) is not None

    def names(self, bucket: str, prefix: str) -> list[str]:
        return sorted(name for name in self._buckets.get(bucket, {}) if name.startswith(prefix))


def _resource(bucket: str, name: str, obj: StoredObject) -> dict[str, Any]:
    stamp = obj.updated.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    crc, md5 = obj.checksums()
    return {
        "kind": "storage#object",
        "id": f"{bucket}/{name}/{obj.generation}",
        "name": name,
        "bucket": bucket,
        "generation": str(obj.generation),
        "metageneration": "1",
        "contentType": obj.content_type,
        "size": str(len(obj.data)),
        # Both, because the client validates an upload against whichever
        # checksum it computed, and which one that is depends on its install.
        "md5Hash": md5,
        "crc32c": crc,
        "storageClass": "STANDARD",
        "timeCreated": stamp,
        "updated": stamp,
    }


def _xml_error(status: int, code: str, message: str) -> Response:
    body = (
        "<?xml version='1.0' encoding='UTF-8'?>"
        f"<Error><Code>{code}</Code><Message>{message}</Message></Error>"
    )
    return Response(body, status_code=status, media_type="application/xml")


def _json_error(status: int, message: str) -> JSONResponse:
    reason = {400: "invalid", 404: "notFound"}.get(status, "error")
    return JSONResponse(
        {"error": {"code": status, "message": message, "errors": [{"reason": reason}]}},
        status_code=status,
    )


def _media(request: Request, obj: StoredObject, extra: dict[str, str]) -> Response:
    """The object's bytes, or the ``Range`` of them asked for."""
    headers = {"Accept-Ranges": "bytes", **extra}
    match = _RANGE.match(request.headers.get("range", ""))
    if match is None:
        crc, md5 = obj.checksums()
        headers["x-goog-hash"] = f"crc32c={crc},md5={md5}"
        return Response(obj.data, media_type=obj.content_type, headers=headers)
    start = int(match.group(1))
    end = min(int(match.group(2) or len(obj.data) - 1), len(obj.data) - 1)
    headers["Content-Range"] = f"bytes {start}-{end}/{len(obj.data)}"
    return Response(
        obj.data[start : end + 1], status_code=206, media_type=obj.content_type, headers=headers
    )


def _split_multipart(content_type: str, body: bytes) -> tuple[dict[str, Any], bytes]:
    """The metadata and media of a ``multipart/related`` upload.

    The object's type is the metadata's ``contentType`` when it has one and
    the media part's own ``Content-Type`` otherwise, as GCS reads it; it is
    folded into the returned metadata.
    """
    boundary = content_type.split("boundary=", 1)[1].strip('"').encode()
    parts = body.split(b"--" + boundary)
    metadata = json.loads(parts[1].split(b"\r\n\r\n", 1)[1])
    media_headers, data = parts[2].split(b"\r\n\r\n", 1)
    for line in media_headers.decode().splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() == "content-type":
            metadata.setdefault("contentType", value.strip())
    return metadata, data.removesuffix(b"\r\n")


def _add_json_api(app: FastAPI, store: Store) -> None:
    @app.get("/storage/v1/b/{bucket}/o")
    async def list_objects(bucket: str, prefix: str = "") -> dict[str, Any]:
        items = []
        for name in store.names(bucket, prefix):
            obj = store.get(bucket, name)
            if obj is not None:
                items.append(_resource(bucket, name, obj))
        return {"kind": "storage#objects", "items": items}

    @app.get("/storage/v1/b/{bucket}/o/{name:path}")
    @app.get("/download/storage/v1/b/{bucket}/o/{name:path}")
    async def get_object(request: Request, bucket: str, name: str) -> Response:
        obj = store.get(bucket, name)
        if obj is None:
            return _json_error(404, f"No such object: {bucket}/{name}")
        if request.query_params.get("alt") == "media":
            return _media(request, obj, {})
        return JSONResponse(_resource(bucket, name, obj))

    @app.delete("/storage/v1/b/{bucket}/o/{name:path}")
    async def delete_object(bucket: str, name: str) -> Response:
        if not store.delete(bucket, name):
            return _json_error(404, f"No such object: {bucket}/{name}")
        return Response(status_code=204)

    @app.post("/upload/storage/v1/b/{bucket}/o")
    async def start_upload(request: Request, bucket: str) -> Response:
        upload_type = request.query_params.get("uploadType")
        body = await request.body()
        if upload_type == "multipart":
            metadata, data = _split_multipart(request.headers["content-type"], body)
            name = metadata.get("name") or request.query_params["name"]
            obj = store.put(bucket, name, data, metadata.get("contentType") or _OCTET_STREAM)
            return JSONResponse(_resource(bucket, name, obj))
        if upload_type == "media":
            name = request.query_params["name"]
            obj = store.put(bucket, name, body, request.headers.get("content-type", _OCTET_STREAM))
            return JSONResponse(_resource(bucket, name, obj))
        if upload_type == "resumable":
            metadata = json.loads(body) if body else {}
            upload_id = uuid.uuid4().hex
            store.sessions[upload_id] = ResumableSession(
                bucket=bucket,
                name=metadata.get("name") or request.query_params["name"],
                content_type=metadata.get("contentType")
                or request.headers.get("x-upload-content-type", _OCTET_STREAM),
            )
            location = (
                f"{str(request.base_url).rstrip('/')}/upload/storage/v1/b/{bucket}/o"
                f"?uploadType=resumable&upload_id={upload_id}"
            )
            return Response(status_code=200, headers={"Location": location})
        return _json_error(400, f"Unsupported uploadType {upload_type}")

    @app.put("/upload/storage/v1/b/{bucket}/o")
    async def upload_chunk(request: Request, upload_id: str) -> Response:
        session = store.sessions.get(upload_id)
        if session is None:
            return _json_error(404, "No such upload session")
        match = _CONTENT_RANGE.match(request.headers.get("content-range", ""))
        if match is None:
            return _json_error(400, "Missing or malformed Content-Range")
        start, _, total = match.groups()
        chunk = await request.body()
        if start is not None:
            del session.data[int(start) :]
            session.data.extend(chunk)
        if total == "*" or len(session.data) < int(total):
            # 308 is how resumable upload says "send the next chunk". With
            # nothing persisted yet GCS sends no Range at all.
            persisted = {"Range": f"bytes=0-{len(session.data) - 1}"} if session.data else {}
            return Response(status_code=308, headers=persisted)
        del store.sessions[upload_id]
        obj = store.put(session.bucket, session.name, bytes(session.data), session.content_type)
        return JSONResponse(_resource(session.bucket, session.name, obj))


def _add_signed_urls(
    app: FastAPI, store: Store, check: Callable[[Request], Response | None]
) -> None:
    @app.put("/{bucket}/{name:path}")
    async def signed_put(request: Request, bucket: str, name: str) -> Response:
        refused = check(request)
        if refused is not None:
            return refused
        length_range = request.headers.get("x-goog-content-length-range")
        body = await request.body()
        if length_range is not None:
            low, _, high = length_range.partition(",")
            if not (low.isdigit() and high.isdigit()):
                return _xml_error(400, "InvalidArgument", "Malformed x-goog-content-length-range.")
            if len(body) > int(high):
                return _xml_error(400, "EntityTooLarge", "Your proposed upload is too large.")
            if len(body) < int(low):
                return _xml_error(400, "EntityTooSmall", "Your proposed upload is too small.")
        obj = store.put(bucket, name, body, request.headers.get("content-type", _OCTET_STREAM))
        return Response(status_code=200, headers={"ETag": f'"{obj.generation}"'})

    @app.get("/{bucket}/{name:path}")
    async def signed_get(request: Request, bucket: str, name: str) -> Response:
        refused = check(request)
        if refused is not None:
            return refused
        obj = store.get(bucket, name)
        if obj is None:
            return _xml_error(404, "NoSuchKey", "The specified key does not exist.")
        extra: dict[str, str] = {}
        disposition = request.query_params.get("response-content-disposition")
        if disposition is not None:
            extra["Content-Disposition"] = disposition
        response = _media(request, obj, extra)
        override = request.query_params.get("response-content-type")
        if override is not None:
            response.headers["Content-Type"] = override
        return response


def create_app(
    *,
    public_key: rsa.RSAPublicKey,
    client_email: str = SERVICE_ACCOUNT_EMAIL,
    cors_origins: list[str],
    clock: Callable[[], datetime] = lambda: datetime.now(tz=UTC),
) -> FastAPI:
    store = Store(clock)

    def check_signature(request: Request) -> Response | None:
        headers: dict[str, list[str]] = {}
        for name, value in request.headers.items():
            headers.setdefault(name.lower(), []).append(value)
        raw_path: bytes = request.scope.get("raw_path") or request.url.path.encode()
        signed = SignedRequest(
            method=request.method,
            raw_path=raw_path.decode(),
            query_string=request.scope["query_string"].decode(),
            headers=headers,
        )
        try:
            verify(signed, public_key=public_key, client_email=client_email, now=clock())
        except SignatureError as refused:
            return _xml_error(refused.status, refused.code, refused.message)
        return None

    app = FastAPI(title="fake gcs", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "PUT", "POST", "DELETE"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition", "Content-Range"],
    )

    @app.get("/_fake/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/token")
    async def token() -> dict[str, Any]:
        return {"access_token": "fake-gcs-token", "expires_in": 3600, "token_type": "Bearer"}

    _add_json_api(app, store)
    # Last: ``/{bucket}/{name:path}`` would otherwise swallow the JSON API.
    _add_signed_urls(app, store, check_signature)
    return app


def app_from_env() -> FastAPI:
    origins = os.environ.get("FAKE_GCS_CORS_ORIGINS", "")
    return create_app(
        public_key=load_public_key(Path(os.environ["FAKE_GCS_CREDENTIALS_DIR"])),
        cors_origins=[origin for origin in origins.split(",") if origin],
    )
