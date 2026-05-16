# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Native companion device enrollment service.

Validates the JWK submitted by the companion at OAuth code-exchange,
computes its RFC 7638 thumbprint, and persists the enrollment via the
:class:`CompanionDeviceRepository`. The DPoP middleware
(THERAPY-6qtr) reads from the same store to verify per-request proofs.

Stage-1 scope only: enrollment + storage + ``last_seen`` updates.
Per-request DPoP signature verification is in the middleware bead.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import TYPE_CHECKING

from ..models.companion_device import CompanionDevice, CompanionEnrollment
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories.companion_device import CompanionDeviceRepository

logger = logging.getLogger(__name__)


class InvalidDeviceJWKError(ValueError):
    """The JWK in the enrollment payload is malformed or unsupported."""


# RFC 7638 §3.2 — required canonical members per key type, in the
# lexicographic order the spec mandates for the thumbprint hash input.
_REQUIRED_BY_KTY: dict[str, tuple[str, ...]] = {
    "EC": ("crv", "kty", "x", "y"),
    "RSA": ("e", "kty", "n"),
    "OKP": ("crv", "kty", "x"),
}

# Curves we accept for device-bound keys. Secure Enclave (Mac) is
# P-256-only; TPM 2.0 (Windows) typically generates RSA-2048 or P-256.
# Ed25519 is unusual from these but standardized — allow it.
_ALLOWED_EC_CURVES = frozenset({"P-256", "P-384", "P-521"})
_ALLOWED_OKP_CURVES = frozenset({"Ed25519", "Ed448"})


def compute_jkt(jwk: dict[str, str]) -> str:
    """Compute the RFC 7638 JWK thumbprint.

    Canonical JSON of the required members (sorted lexicographically,
    no whitespace) → SHA-256 → base64url, no padding.
    """
    kty = jwk.get("kty")
    if kty not in _REQUIRED_BY_KTY:
        raise InvalidDeviceJWKError(f"unsupported JWK kty: {kty!r}")

    required = _REQUIRED_BY_KTY[kty]
    canonical = {member: jwk[member] for member in required}
    serialized = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode("ascii")
    digest = hashlib.sha256(serialized).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def validate_device_jwk(jwk: dict[str, str]) -> None:
    """Reject obviously-bad keys before storing them.

    We don't verify the curve point is on-curve here — the DPoP
    middleware will fail to verify proofs against a garbage key,
    which is a downstream consistency check. This is purely structural.
    """
    kty = jwk.get("kty")
    if kty not in _REQUIRED_BY_KTY:
        raise InvalidDeviceJWKError(f"unsupported JWK kty: {kty!r}")

    required = _REQUIRED_BY_KTY[kty]
    missing = [m for m in required if m not in jwk]
    if missing:
        raise InvalidDeviceJWKError(f"JWK missing required members for kty={kty}: {missing}")

    for member in required:
        value = jwk[member]
        if not isinstance(value, str) or not value:
            raise InvalidDeviceJWKError(f"JWK member {member!r} must be a non-empty string")

    if kty == "EC" and jwk["crv"] not in _ALLOWED_EC_CURVES:
        raise InvalidDeviceJWKError(f"EC curve not allowed: {jwk['crv']!r}")
    if kty == "OKP" and jwk["crv"] not in _ALLOWED_OKP_CURVES:
        raise InvalidDeviceJWKError(f"OKP curve not allowed: {jwk['crv']!r}")


class CompanionDeviceService:
    def __init__(self, repo: CompanionDeviceRepository) -> None:
        self._repo = repo

    def enroll(self, user_id: str, payload: CompanionEnrollment) -> CompanionDevice:
        """Validate + persist a device enrollment. Returns the stored row."""
        validate_device_jwk(payload.device_public_key_jwk)
        jkt = compute_jkt(payload.device_public_key_jwk)
        now = utc_now()
        device = CompanionDevice(
            install_id=payload.install_id,
            user_id=user_id,
            device_public_key_jwk=payload.device_public_key_jwk,
            jkt=jkt,
            key_storage=payload.key_storage,
            platform=payload.platform,
            os_version=payload.os_version,
            hostname_hash=payload.hostname_hash,
            enrolled_at=now,
            last_seen=now,
            revoked_at=None,
        )
        self._repo.upsert(device)
        logger.info(
            "companion_device_enrolled user_id=%s install_id=%s platform=%s key_storage=%s jkt=%s",
            user_id,
            payload.install_id,
            payload.platform,
            payload.key_storage,
            jkt,
        )
        return device


def get_companion_device_service() -> CompanionDeviceService:
    """Build the service against whichever repo backend is configured.

    Falls back to an in-memory repo when no Postgres session is in
    scope (tests, dev harnesses). The fallback drops state on restart
    and is never used in production.
    """
    from ..repositories.companion_device import InMemoryCompanionDeviceRepository

    try:
        from ..db import get_db_session
        from ..repositories.postgres.companion_device import (
            PostgresCompanionDeviceRepository,
        )

        return CompanionDeviceService(PostgresCompanionDeviceRepository(get_db_session()))
    except RuntimeError:
        return CompanionDeviceService(InMemoryCompanionDeviceRepository())
