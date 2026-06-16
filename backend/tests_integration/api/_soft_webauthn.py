# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A minimal in-process software WebAuthn authenticator for integration tests.

Produces **real** registration-attestation and authentication-assertion
responses signed by a locally generated P-256 (ES256) key, so the backend's
genuine ``py_webauthn`` verify path runs end-to-end — the security-critical
crypto is exercised, never mocked (see ``docs/internal/passkey-auth-e2e-design.md``,
"What to avoid"). This is the API-layer (PABLO-egm.6) analogue of the Chrome
DevTools virtual authenticator the browser e2e (PABLO-egm.7) drives.

It implements just enough of CTAP2 + the ``none`` attestation format to satisfy
``verify_registration_response`` / ``verify_authentication_response``:

* registration → ``attestationObject`` = CBOR ``{fmt: "none", attStmt: {},
  authData}`` where ``authData`` carries attested-credential-data (AAGUID +
  credential id + COSE public key),
* authentication → ``authenticatorData`` + an ECDSA signature over
  ``authData || SHA-256(clientDataJSON)``.

Failure injection (the negative matrix) is parameterized: flip user
verification off, replay a sign counter, or sign for the wrong origin.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

# authenticatorData flag bits (WebAuthn §6.1).
_FLAG_UP = 0x01  # user present
_FLAG_UV = 0x04  # user verified
_FLAG_AT = 0x40  # attested credential data included

# All-zero AAGUID — the privacy-preserving sentinel a platform authenticator
# reports; the service normalizes it to NULL on store.
_ZERO_AAGUID = b"\x00" * 16


def _cose_es256_public_key(public_key: ec.EllipticCurvePublicKey) -> bytes:
    """Encode a P-256 public key as a COSE_Key (the shape registration stores)."""
    numbers = public_key.public_numbers()
    return cbor2.dumps(
        {
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),  # x coordinate
            -3: numbers.y.to_bytes(32, "big"),  # y coordinate
        }
    )


class SoftWebAuthnAuthenticator:
    """A single software authenticator holding one credential keypair."""

    def __init__(self, *, user_verified: bool = True) -> None:
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0
        self.user_verified = user_verified
        self.user_handle: bytes | None = None

    @property
    def credential_id_b64(self) -> str:
        return bytes_to_base64url(self.credential_id)

    @staticmethod
    def _client_data(ceremony_type: str, challenge_b64: str, origin: str) -> bytes:
        # The challenge in clientDataJSON is the base64url of the raw challenge
        # bytes — exactly what the begin response already hands us, so it round
        # trips through the server's SHA-256 single-use lookup unchanged.
        return json.dumps(
            {
                "type": ceremony_type,
                "challenge": challenge_b64,
                "origin": origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    def _authenticator_data(self, rp_id: str, flags: int, *, attested: bool) -> bytes:
        data = (
            hashlib.sha256(rp_id.encode("utf-8")).digest()
            + bytes([flags])
            + struct.pack(">I", self.sign_count)
        )
        if attested:
            data += (
                _ZERO_AAGUID
                + struct.pack(">H", len(self.credential_id))
                + self.credential_id
                + _cose_es256_public_key(self._private_key.public_key())
            )
        return data

    def create(self, options: dict[str, Any], *, origin: str) -> dict[str, Any]:
        """Answer ``register/begin`` options with a real attestation response."""
        rp_id = options["rp"]["id"]
        # Resident-key / discoverable credential: remember the user handle so
        # the later usernameless assertion can echo it back.
        self.user_handle = self.credential_id  # opaque; the server keys on credential id
        flags = _FLAG_UP | _FLAG_AT | (_FLAG_UV if self.user_verified else 0)
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        auth_data = self._authenticator_data(rp_id, flags, attested=True)
        attestation_object = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation_object),
                "transports": ["internal"],
            },
            "clientExtensionResults": {},
        }

    def get(
        self,
        options: dict[str, Any],
        *,
        origin: str,
        sign_count: int | None = None,
        user_verified: bool | None = None,
    ) -> dict[str, Any]:
        """Answer ``authenticate/begin`` options with a real signed assertion.

        ``sign_count`` overrides the counter (set it ``<=`` the stored value to
        simulate a cloned authenticator); ``user_verified`` overrides the UV
        flag for this assertion only (set ``False`` to prove UV-required
        rejection without re-enrolling).
        """
        rp_id = options["rpId"]
        self.sign_count = sign_count if sign_count is not None else self.sign_count + 1
        uv = self.user_verified if user_verified is None else user_verified
        flags = _FLAG_UP | (_FLAG_UV if uv else 0)
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        auth_data = self._authenticator_data(rp_id, flags, attested=False)
        signature = self._private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        response: dict[str, Any] = {
            "clientDataJSON": bytes_to_base64url(client_data),
            "authenticatorData": bytes_to_base64url(auth_data),
            "signature": bytes_to_base64url(signature),
        }
        if self.user_handle is not None:
            response["userHandle"] = bytes_to_base64url(self.user_handle)
        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "type": "public-key",
            "response": response,
            "clientExtensionResults": {},
        }
