# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""AES-256-GCM token encryption for HIPAA-compliant OAuth token storage.

HIPAA Compliance: OAuth tokens provide access to therapist calendars which
may contain PHI (patient names in appointment titles). Tokens are encrypted
at rest with AES-256-GCM before storage in Firestore.
"""

from __future__ import annotations

import base64
import json
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

AES_KEY_BYTES = 32  # 256 bits
NONCE_BYTES = 12  # 96-bit nonce per NIST recommendation for GCM


class TokenEncryptionError(Exception):
    """Raised when token encryption or decryption fails."""


def _get_encryption_key() -> bytes:
    """Load the AES-256 encryption key from environment.

    The key must be a 32-byte value, base64-encoded in the environment variable.
    """
    raw = os.environ.get("GOOGLE_CALENDAR_ENCRYPTION_KEY", "")
    if not raw:
        raise TokenEncryptionError("GOOGLE_CALENDAR_ENCRYPTION_KEY environment variable is not set")
    try:
        key = base64.b64decode(raw)
    except Exception as exc:
        raise TokenEncryptionError("Invalid base64 in GOOGLE_CALENDAR_ENCRYPTION_KEY") from exc

    if len(key) != AES_KEY_BYTES:
        raise TokenEncryptionError(f"Encryption key must be {AES_KEY_BYTES} bytes, got {len(key)}")
    return key


def encrypt_tokens(token_data: dict[str, str]) -> str:
    """Encrypt OAuth token data with AES-256-GCM.

    Returns a base64-encoded string containing nonce + ciphertext + tag.
    """
    key = _get_encryption_key()
    plaintext = json.dumps(token_data).encode("utf-8")
    nonce = os.urandom(NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    # nonce (12) + ciphertext+tag (variable)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_tokens(encrypted_b64: str) -> dict[str, str]:
    """Decrypt AES-256-GCM encrypted OAuth tokens.

    Expects the base64-encoded nonce + ciphertext + tag produced by encrypt_tokens().
    """
    key = _get_encryption_key()
    try:
        raw = base64.b64decode(encrypted_b64)
    except Exception as exc:
        raise TokenEncryptionError("Invalid base64 in encrypted token data") from exc

    if len(raw) < NONCE_BYTES + 16:  # nonce + minimum GCM tag
        raise TokenEncryptionError("Encrypted data too short")

    nonce = raw[:NONCE_BYTES]
    ciphertext = raw[NONCE_BYTES:]
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise TokenEncryptionError(
            "Token decryption failed — key mismatch or data corrupted"
        ) from exc

    return json.loads(plaintext.decode("utf-8"))  # type: ignore[no-any-return]


def generate_encryption_key() -> str:
    """Generate a new random AES-256 key, base64-encoded.

    Utility for initial setup / key rotation.
    """
    return base64.b64encode(os.urandom(AES_KEY_BYTES)).decode("ascii")
