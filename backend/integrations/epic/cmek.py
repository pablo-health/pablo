# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""CMEK envelope-encryption key provider (server-readable, crypto-shreddable).

A fresh per-record data key (DEK) encrypts the record; Cloud KMS wraps that
DEK with a per-patient key-encryption key (KEK), and only the wrapped DEK is
stored. Pablo (with IAM on the KEK) can unwrap and decrypt — enabling
server-side AI on the patient's record — and destroying the KEK
crypto-shreds every record wrapped under it.

Only the wrap/unwrap calls touch KMS (one each per record), so cost tracks
record count, not data size. Production needs ``google-cloud-kms``
(``poetry add google-cloud-kms``); tests inject a fake client.
"""

import base64
from typing import Any

from integrations.epic.vault import JsonDict, Vault, generate_key

_SCHEME = "cmek-envelope"


class CmekKeyProvider:
    """Wraps a per-record DEK with a per-patient Cloud KMS key."""

    def __init__(self, key_name: str, client: Any | None = None) -> None:
        # key_name: projects/P/locations/L/keyRings/R/cryptoKeys/K
        self._key_name = key_name
        self._client = client if client is not None else _default_client()

    def issue(self) -> tuple[Vault, JsonDict]:
        dek = generate_key()
        wrapped = self._client.encrypt(name=self._key_name, plaintext=dek).ciphertext
        material: JsonDict = {
            "scheme": _SCHEME,
            "kms_key": self._key_name,
            "wrapped_dek": base64.b64encode(wrapped).decode("ascii"),
        }
        return Vault(dek), material

    def restore(self, key_material: JsonDict) -> Vault:
        wrapped = base64.b64decode(str(key_material["wrapped_dek"]))
        key_name = str(key_material.get("kms_key", self._key_name))
        dek = self._client.decrypt(name=key_name, ciphertext=wrapped).plaintext
        return Vault(dek)


def _default_client() -> Any:
    try:
        from google.cloud import kms  # noqa: PLC0415 - optional prod dep, imported lazily
    except ImportError as exc:
        raise RuntimeError(
            "CMEK requires the google-cloud-kms package — run `poetry add google-cloud-kms`."
        ) from exc
    return kms.KeyManagementServiceClient()
