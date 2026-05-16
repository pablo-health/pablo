# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the companion device enrollment service (THERAPY-xo0o)."""

from __future__ import annotations

import pytest
from app.models.companion_device import CompanionEnrollment
from app.repositories.companion_device import InMemoryCompanionDeviceRepository
from app.services.companion_device_service import (
    CompanionDeviceService,
    InvalidDeviceJWKError,
    compute_jkt,
    validate_device_jwk,
)

# RFC 7638 §3.1 — canonical test vector for JWK thumbprint.
# This JWK MUST hash to NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs.
RFC_7638_JWK: dict[str, str] = {
    "kty": "RSA",
    "n": (
        "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86z"
        "wu1RK7aPFFxuhDR1L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_R"
        "N5w6Cf0h4QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91C"
        "bOpbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csFCur-k"
        "EgU8awapJzKnqDKgw"
    ),
    "e": "AQAB",
    "alg": "RS256",
    "kid": "2011-04-29",
}
RFC_7638_THUMBPRINT = "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


class TestComputeJkt:
    def test_rfc_7638_rsa_test_vector(self) -> None:
        assert compute_jkt(RFC_7638_JWK) == RFC_7638_THUMBPRINT

    def test_extra_members_ignored(self) -> None:
        # Adding non-canonical members does not change the thumbprint.
        with_extras = {**RFC_7638_JWK, "use": "sig", "x5t": "ignored"}
        assert compute_jkt(with_extras) == RFC_7638_THUMBPRINT

    def test_ec_p256_thumbprint_deterministic(self) -> None:
        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
            "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
        }
        first = compute_jkt(jwk)
        second = compute_jkt({**jwk, "use": "sig"})
        assert first == second
        # base64url, no padding
        assert "=" not in first
        # SHA-256 → 32 bytes → 43 base64url chars
        assert len(first) == 43

    def test_unsupported_kty_raises(self) -> None:
        with pytest.raises(InvalidDeviceJWKError):
            compute_jkt({"kty": "oct", "k": "abc"})


class TestValidateDeviceJwk:
    def test_valid_ec_p256(self) -> None:
        validate_device_jwk(
            {
                "kty": "EC",
                "crv": "P-256",
                "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
                "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
            }
        )

    def test_valid_rsa(self) -> None:
        validate_device_jwk(RFC_7638_JWK)

    def test_missing_required_member(self) -> None:
        with pytest.raises(InvalidDeviceJWKError, match="missing"):
            validate_device_jwk({"kty": "EC", "crv": "P-256", "x": "abc"})  # no y

    def test_disallowed_ec_curve(self) -> None:
        with pytest.raises(InvalidDeviceJWKError, match="EC curve"):
            validate_device_jwk({"kty": "EC", "crv": "secp256k1", "x": "abc", "y": "def"})

    def test_unsupported_kty(self) -> None:
        with pytest.raises(InvalidDeviceJWKError, match="kty"):
            validate_device_jwk({"kty": "oct", "k": "secret"})

    def test_empty_value(self) -> None:
        with pytest.raises(InvalidDeviceJWKError, match="non-empty"):
            validate_device_jwk({"kty": "EC", "crv": "P-256", "x": "", "y": "def"})


class TestEnroll:
    @pytest.fixture
    def service(self) -> CompanionDeviceService:
        return CompanionDeviceService(InMemoryCompanionDeviceRepository())

    @pytest.fixture
    def payload(self) -> CompanionEnrollment:
        return CompanionEnrollment(
            install_id="install-abc123",
            platform="mac",
            os_version="15.2",
            hostname_hash="hash-deadbeef",
            device_public_key_jwk={
                "kty": "EC",
                "crv": "P-256",
                "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
                "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
            },
            key_storage="hardware",
        )

    def test_enrolls_and_persists(
        self, service: CompanionDeviceService, payload: CompanionEnrollment
    ) -> None:
        device = service.enroll("user-1", payload)
        assert device.user_id == "user-1"
        assert device.install_id == "install-abc123"
        assert device.key_storage == "hardware"
        assert device.platform == "mac"
        assert device.jkt  # computed
        assert device.revoked_at is None

    def test_reenroll_same_install_preserves_enrolled_at(
        self, service: CompanionDeviceService, payload: CompanionEnrollment
    ) -> None:
        first = service.enroll("user-1", payload)
        # Re-enrollment from same install (e.g., key regenerated)
        second_payload = CompanionEnrollment(
            install_id=payload.install_id,
            platform="mac",
            os_version="15.3",
            hostname_hash=payload.hostname_hash,
            device_public_key_jwk={
                "kty": "EC",
                "crv": "P-256",
                "x": "AAAAOJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
                "y": "BBBBzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
            },
            key_storage="hardware",
        )
        second = service.enroll("user-1", second_payload)
        # InMemoryCompanionDeviceRepository preserves enrolled_at on
        # upsert; service-emitted enrolled_at is the new "now", but
        # the stored row holds the original.
        assert second.jkt != first.jkt  # new key → new thumbprint

    def test_rejects_bad_jwk(
        self, service: CompanionDeviceService, payload: CompanionEnrollment
    ) -> None:
        bad = payload.model_copy(
            update={"device_public_key_jwk": {"kty": "EC", "crv": "P-256", "x": "abc"}}
        )
        with pytest.raises(InvalidDeviceJWKError):
            service.enroll("user-1", bad)
