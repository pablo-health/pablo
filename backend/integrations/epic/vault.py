# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Symmetric encryption for the patient-owned store.

The patient-owned PHR copy is encrypted at rest with a key the *patient*
controls — Pablo persists ciphertext, never the key. A key can be derived
from a patient passphrase (so Pablo never sees it) or generated and handed
to the patient. Uses Fernet (AES-128-CBC + HMAC, authenticated) so a wrong
key or tampered ciphertext fails loudly rather than returning garbage.
"""

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

# scrypt work factors — interactive-grade, ample for a per-patient key.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_BYTES = 32


class VaultError(Exception):
    """Raised when decryption fails (wrong key or corrupted ciphertext)."""


def generate_key() -> bytes:
    """Generate a fresh random Fernet key for the patient to hold."""
    return Fernet.generate_key()


def derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a Fernet key from a patient passphrase + salt (scrypt)."""
    kdf = Scrypt(salt=salt, length=_KEY_BYTES, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    raw = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


class Vault:
    """Encrypts/decrypts bytes with a patient-held key."""

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, data: bytes) -> bytes:
        return self._fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        try:
            return self._fernet.decrypt(token)
        except InvalidToken as exc:
            raise VaultError("decryption failed — wrong key or corrupted data") from exc
