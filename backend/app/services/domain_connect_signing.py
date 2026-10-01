# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Building and signing a Domain Connect synchronous apply link.

The link sends the practice to its DNS provider with a template to apply and
the values to fill it with. A template that names a ``syncPubKeyDomain``
must be signed, and the provider checks the signature against the public key
this deployment publishes in DNS at ``<key host>.<syncPubKeyDomain>``. The
procedure (draft-ietf-dconn-domainconnect, "Signing Procedure"):

1. Percent-encode every parameter name and value except ``sig`` and ``key``
   (RFC 3986: only ``A-Z a-z 0-9 - . _ ~`` stay as they are).
2. Sort by the encoded name and join as ``name=value`` with ``&``. That
   string is what is signed: RS256, RSASSA-PKCS1-v1_5 over SHA-256.
3. Append ``&sig=<standard base64 of the signature, percent-encoded>`` and
   ``&key=<key host>``.

The provider verifies the query string exactly as received, so the string
signed here is the string sent, never re-encoded or reordered afterwards.

Signing is behind :class:`Signer`: :class:`KmsSigner` asks Cloud KMS, where
the private key stays, and :class:`RsaKeySigner` holds a key in memory (tests,
or an install that keeps its key elsewhere).
"""

from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import quote, unquote

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_UNRESERVED = "-._~"


class Signer(Protocol):
    def sign(self, message: bytes) -> bytes:
        """An RS256 signature over *message*."""
        ...


class RsaKeySigner:
    """Signs with an RSA private key held in memory."""

    def __init__(self, private_key: rsa.RSAPrivateKey) -> None:
        self._key = private_key

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message, padding.PKCS1v15(), hashes.SHA256())


class KmsSigner:
    """Signs with a Cloud KMS asymmetric key version
    (``RSA_SIGN_PKCS1_*_SHA256``). KMS signs a digest, so the SHA-256 is
    taken here."""

    def __init__(self, key_version: str, client: Any | None = None) -> None:
        self._key_version = key_version
        self._client = client

    def _kms(self) -> Any:
        if self._client is None:
            from google.cloud import kms

            self._client = kms.KeyManagementServiceClient()
        return self._client

    def sign(self, message: bytes) -> bytes:
        digest = hashlib.sha256(message).digest()
        response = self._kms().asymmetric_sign(
            request={"name": self._key_version, "digest": {"sha256": digest}}
        )
        return bytes(response.signature)


def _encode(value: str) -> str:
    return quote(value, safe=_UNRESERVED)


def canonical_query(params: Mapping[str, str]) -> str:
    """The string that is signed: encoded pairs sorted by encoded name."""
    pairs = sorted((_encode(name), _encode(value)) for name, value in params.items())
    return "&".join(f"{name}={value}" for name, value in pairs)


def signed_query(params: Mapping[str, str], signer: Signer, key_host: str) -> str:
    """The canonical query with its ``sig`` and ``key`` appended."""
    canonical = canonical_query(params)
    signature = base64.b64encode(signer.sign(canonical.encode("ascii"))).decode("ascii")
    return f"{canonical}&sig={quote(signature, safe='')}&key={_encode(key_host)}"


def apply_url(
    url_sync_ux: str,
    provider_id: str,
    service_id: str,
    params: Mapping[str, str],
    signer: Signer,
    key_host: str,
) -> str:
    """The signed synchronous apply link for one template."""
    base = url_sync_ux.rstrip("/")
    path = (
        f"/v2/domainTemplates/providers/{_encode(provider_id)}/services/{_encode(service_id)}/apply"
    )
    return f"{base}{path}?{signed_query(params, signer, key_host)}"


# ── The provider's side, for checking our own links ──────────────────────


class SignatureError(ValueError):
    """A signed query or a published key that does not check out."""


def public_key_from_txt(records: Iterable[str]) -> rsa.RSAPublicKey:
    """The public key published as TXT records ``p=<n>,a=RS256,d=<base64>``.

    Fragments are joined in ascending ``p`` order, whatever order DNS
    returned them in; the result is a base64 DER SubjectPublicKeyInfo.
    """
    fragments: list[tuple[int, str]] = []
    for record in records:
        fields = dict(part.split("=", 1) for part in record.split(",") if "=" in part)
        if fields.get("a", "RS256") != "RS256":
            raise SignatureError(f"unsupported algorithm {fields['a']!r}")
        try:
            fragments.append((int(fields["p"]), fields["d"]))
        except (KeyError, ValueError) as e:
            raise SignatureError("key record without p= and d=") from e
    if not fragments:
        raise SignatureError("no key records")
    try:
        der = base64.b64decode("".join(data for _, data in sorted(fragments)))
        key = serialization.load_der_public_key(der)
    except ValueError as e:
        raise SignatureError("published key does not decode") from e
    if not isinstance(key, rsa.RSAPublicKey):
        raise SignatureError("published key is not RSA")
    return key


def verify_signed_query(query: str, public_key: rsa.RSAPublicKey) -> None:
    """Check a received query string the way a DNS provider does: drop the
    ``sig`` and ``key`` pairs without touching the rest, then verify ``sig``
    over what is left. Raises :class:`SignatureError`."""
    kept: list[str] = []
    sig: str | None = None
    for pair in query.split("&"):
        name = pair.split("=", 1)[0]
        if name == "sig":
            sig = pair.split("=", 1)[1]
        elif name != "key":
            kept.append(pair)
    if sig is None:
        raise SignatureError("no sig")
    verify(public_key, "&".join(kept).encode("ascii"), base64.b64decode(unquote(sig)))


def verify(public_key: rsa.RSAPublicKey, message: bytes, signature: bytes) -> None:
    from cryptography.exceptions import InvalidSignature

    try:
        public_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise SignatureError("signature does not verify") from e
