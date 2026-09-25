# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""V4 signed-URL verification for the fake GCS server, and its credentials.

The end-to-end stack signs its storage URLs with the real
``google-cloud-storage`` signer, so this side has to check them the way
Google does or a signing regression would pass the suite. The recipe is
Google's published one (cloud.google.com/storage/docs/authentication/signatures):

* canonical request — method, path as sent, the query minus
  ``X-Goog-Signature`` re-encoded and sorted, the headers named by
  ``X-Goog-SignedHeaders`` as ``name:value`` lines, that list again, and the
  payload hash (``UNSIGNED-PAYLOAD`` unless ``x-goog-content-sha256`` is signed);
* string to sign — ``GOOG4-RSA-SHA256``, ``X-Goog-Date``, the credential
  scope, and the hex SHA-256 of the canonical request;
* the signature is hex RSASSA-PKCS1-v1_5 over that, with SHA-256.

The server only ever holds the public half. The private half exists so the
backend can sign, and is minted fresh per stack by ``python
fake_gcs_signing.py <dir>`` — nothing a key scanner would find is committed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

if TYPE_CHECKING:
    from collections.abc import Mapping

ALGORITHM = "GOOG4-RSA-SHA256"
SERVICE_ACCOUNT_EMAIL = "e2e-storage@demo-pablo-e2e.iam.gserviceaccount.com"
CREDENTIALS_FILE = "service-account.json"
PUBLIC_KEY_FILE = "public.pem"
# Google's ceiling on X-Goog-Expires: seven days.
_MAX_EXPIRES_SECONDS = 7 * 24 * 3600
_REQUIRED_PARAMS = (
    "X-Goog-Algorithm",
    "X-Goog-Credential",
    "X-Goog-Date",
    "X-Goog-Expires",
    "X-Goog-SignedHeaders",
)


class SignatureError(Exception):
    """A signed request GCS would refuse, with the status and code it uses."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SignedRequest:
    """The parts of a request a V4 signature covers.

    ``raw_path`` is the path exactly as sent (still percent-encoded), and
    ``headers`` is keyed by lower-cased name with every value received.
    """

    method: str
    raw_path: str
    query_string: str
    headers: Mapping[str, list[str]]

    @property
    def params(self) -> dict[str, str]:
        return dict(parse_qsl(self.query_string, keep_blank_values=True))


def canonical_query(query_string: str) -> str:
    """The query as the signer encoded it: every parameter but the signature."""
    pairs = parse_qsl(query_string, keep_blank_values=True)
    return "&".join(
        sorted(
            f"{quote(name, safe='~')}={quote(value, safe='~')}"
            for name, value in pairs
            if name != "X-Goog-Signature"
        )
    )


def canonical_headers(signed_headers: str, headers: Mapping[str, list[str]]) -> str:
    """``name:value`` lines for the signed headers, whitespace folded, repeats joined."""
    lines = []
    for name in signed_headers.split(";"):
        values = [" ".join(value.split()) for value in headers.get(name, [])]
        lines.append(f"{name}:{','.join(values)}")
    return "\n".join(lines) + "\n"


def string_to_sign(request: SignedRequest) -> str:
    """What the signer signed, rebuilt from what arrived."""
    params = request.params
    signed_headers = params["X-Goog-SignedHeaders"]
    content_sha256 = request.headers.get("x-goog-content-sha256")
    payload = (
        content_sha256[0]
        if content_sha256 and "x-goog-content-sha256" in signed_headers.split(";")
        else "UNSIGNED-PAYLOAD"
    )
    canonical_request = "\n".join(
        [
            request.method,
            request.raw_path,
            canonical_query(request.query_string),
            canonical_headers(signed_headers, request.headers),
            signed_headers,
            payload,
        ]
    )
    scope = params["X-Goog-Credential"].split("/", 1)[1]
    return "\n".join(
        [
            ALGORITHM,
            params["X-Goog-Date"],
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )


def verify(
    request: SignedRequest,
    *,
    public_key: rsa.RSAPublicKey,
    client_email: str,
    now: datetime,
) -> None:
    """Raise :class:`SignatureError` unless the request carries a live, valid V4 signature."""
    params = request.params
    if "X-Goog-Signature" not in params:
        # No signature at all is an anonymous request, which a private bucket refuses.
        raise SignatureError(403, "AccessDenied", "Anonymous caller does not have access.")
    missing = [name for name in _REQUIRED_PARAMS if name not in params]
    if missing:
        raise SignatureError(400, "InvalidArgument", f"Missing {', '.join(missing)}.")
    if params["X-Goog-Algorithm"] != ALGORITHM:
        raise SignatureError(400, "InvalidArgument", "Unsupported X-Goog-Algorithm.")

    email, _, scope = params["X-Goog-Credential"].partition("/")
    try:
        signed_at = datetime.strptime(params["X-Goog-Date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        expires = int(params["X-Goog-Expires"])
    except ValueError as exc:
        raise SignatureError(400, "InvalidArgument", "Malformed X-Goog-Date/Expires.") from exc
    if scope != f"{params['X-Goog-Date'][:8]}/auto/storage/goog4_request":
        raise SignatureError(400, "InvalidArgument", "Malformed X-Goog-Credential scope.")
    if not 0 < expires <= _MAX_EXPIRES_SECONDS:
        raise SignatureError(400, "InvalidArgument", "X-Goog-Expires out of range.")
    if now > signed_at + timedelta(seconds=expires):
        raise SignatureError(400, "ExpiredToken", "Request has expired.")
    if email != client_email:
        raise SignatureError(403, "AccessDenied", f"Unknown signer {email}.")

    expected = string_to_sign(request)
    try:
        public_key.verify(
            bytes.fromhex(params["X-Goog-Signature"]),
            expected.encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except (ValueError, InvalidSignature) as exc:
        raise SignatureError(
            403,
            "SignatureDoesNotMatch",
            "The request signature we calculated does not match the signature you provided.",
        ) from exc


def mint_credentials(directory: Path, *, token_uri: str) -> None:
    """Write a throwaway service-account key and its public half, once.

    Idempotent: a second run leaves an existing key alone, so a stack brought
    up again over its old volume does not hand the backend a key the already
    running fake never loaded.
    """
    credentials_path = directory / CREDENTIALS_FILE
    if credentials_path.exists():
        return
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    service_account = {
        "type": "service_account",
        "project_id": "demo-pablo-e2e",
        "private_key_id": "e2e",
        "private_key": private_pem,
        "client_email": SERVICE_ACCOUNT_EMAIL,
        "client_id": "0",
        "token_uri": token_uri,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / PUBLIC_KEY_FILE).write_bytes(public_pem)
    # Written last: its presence is what marks the pair complete.
    credentials_path.write_text(json.dumps(service_account))
    for path in (credentials_path, directory / PUBLIC_KEY_FILE):
        # Read by the backend and the fake, which run as other users.
        path.chmod(0o644)


def load_public_key(directory: Path) -> rsa.RSAPublicKey:
    key = serialization.load_pem_public_key((directory / PUBLIC_KEY_FILE).read_bytes())
    if not isinstance(key, rsa.RSAPublicKey):
        raise TypeError("the fake GCS signer key must be RSA")
    return key


if __name__ == "__main__":
    mint_credentials(
        Path(sys.argv[1]),
        token_uri=os.environ.get("FAKE_GCS_TOKEN_URI", "http://localhost:9000/token"),
    )
