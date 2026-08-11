# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for pre-auth endpoints (native code exchange)."""

from collections.abc import Generator
from typing import Any, ClassVar
from unittest.mock import MagicMock, patch

import pytest
from app.main import app
from app.rate_limit import reset_preauth_limiter
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Reset the rate limiter between tests to prevent 429s."""
    reset_preauth_limiter()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


class TestNativeCodeExchange:
    """POST /api/auth/native/code and /api/auth/native/exchange"""

    REDIRECT_URI = "pablohealth://callback"

    @pytest.fixture(autouse=True)
    def _mock_firebase(self) -> Generator[None]:
        """Mock Firebase init and auth for all native code exchange tests."""
        with (
            patch("app.routes.auth.initialize_firebase_app"),
            patch("app.routes.auth.firebase_auth") as mock_auth,
        ):
            mock_auth.verify_id_token.return_value = {"uid": "user1"}
            self.mock_firebase_auth = mock_auth
            yield

    def test_create_code_valid_custom_scheme(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        assert resp.status_code == 200
        assert "code" in resp.json()
        assert len(resp.json()["code"]) > 0

    def test_create_code_valid_localhost(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": "http://localhost:12345/callback",
            },
        )
        assert resp.status_code == 200

    def test_create_code_valid_loopback_ip(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": "http://127.0.0.1:54321/callback",
            },
        )
        assert resp.status_code == 200

    def test_create_code_invalid_redirect_uri(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": "https://evil.com/steal",
            },
        )
        assert resp.status_code == 400

    def test_create_code_invalid_token_rejected(self, client: TestClient) -> None:
        self.mock_firebase_auth.verify_id_token.side_effect = Exception("invalid token")
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "forged_token",
                "refresh_token": "ref_tok",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        assert resp.status_code == 401

    def test_exchange_valid_code(self, client: TestClient) -> None:
        # Create a code
        create_resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "my_id_token",
                "refresh_token": "my_refresh_token",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        code = create_resp.json()["code"]

        # Exchange it
        exchange_resp = client.post(
            "/api/auth/native/exchange",
            json={"code": code, "redirect_uri": self.REDIRECT_URI},
        )
        assert exchange_resp.status_code == 200
        data = exchange_resp.json()
        expected_id = "my_id_token"
        expected_ref = "my_refresh_token"
        assert data["id_token"] == expected_id
        assert data["refresh_token"] == expected_ref

    def test_exchange_redirect_uri_mismatch(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "tok",
                "refresh_token": "ref",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        code = create_resp.json()["code"]

        # Exchange with wrong redirect_uri
        resp = client.post(
            "/api/auth/native/exchange",
            json={"code": code, "redirect_uri": "http://localhost:9999/evil"},
        )
        assert resp.status_code == 400
        assert "mismatch" in resp.json()["error"]["message"]

    def test_exchange_single_use(self, client: TestClient) -> None:
        create_resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "tok",
                "refresh_token": "ref",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        code = create_resp.json()["code"]

        # First exchange succeeds
        assert (
            client.post(
                "/api/auth/native/exchange",
                json={"code": code, "redirect_uri": self.REDIRECT_URI},
            ).status_code
            == 200
        )

        # Second exchange fails (code consumed)
        assert (
            client.post(
                "/api/auth/native/exchange",
                json={"code": code, "redirect_uri": self.REDIRECT_URI},
            ).status_code
            == 400
        )

    def test_exchange_invalid_code(self, client: TestClient) -> None:
        resp = client.post(
            "/api/auth/native/exchange",
            json={"code": "nonexistent-code", "redirect_uri": self.REDIRECT_URI},
        )
        assert resp.status_code == 400


class TestCompanionDeviceEnrollment:
    """Enrollment payload submitted alongside /native/exchange (THERAPY-xo0o)."""

    REDIRECT_URI = "pablohealth://callback"
    VALID_ENROLLMENT: ClassVar[dict[str, Any]] = {
        "install_id": "install-abc123",
        "platform": "mac",
        "os_version": "15.2",
        "hostname_hash": "abc123",
        "device_public_key_jwk": {
            "kty": "EC",
            "crv": "P-256",
            "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
            "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
        },
        "key_storage": "hardware",
    }

    @pytest.fixture(autouse=True)
    def _mock_firebase(self) -> Generator[None]:
        with (
            patch("app.routes.auth.initialize_firebase_app"),
            patch("app.routes.auth.firebase_auth") as mock_auth,
        ):
            mock_auth.verify_id_token.return_value = {"uid": "fb-user-1"}
            yield

    @pytest.fixture
    def client(self) -> TestClient:
        return TestClient(app)

    def _create_code(self, client: TestClient) -> str:
        resp = client.post(
            "/api/auth/native/code",
            json={
                "id_token": "id_tok",
                "refresh_token": "ref_tok",
                "redirect_uri": self.REDIRECT_URI,
            },
        )
        assert resp.status_code == 200
        code: str = resp.json()["code"]
        return code

    def test_exchange_with_enrollment_persists_device(self, client: TestClient) -> None:
        code = self._create_code(client)

        mock_identity_repo = MagicMock()
        mock_identity_repo.resolve_or_create.return_value = "pablo-user-1"
        mock_service = MagicMock()

        with (
            patch(
                "app.routes.auth.get_identity_repository",
                return_value=mock_identity_repo,
            ),
            patch(
                "app.routes.auth.get_companion_device_service",
                return_value=mock_service,
            ),
        ):
            resp = client.post(
                "/api/auth/native/exchange",
                json={
                    "code": code,
                    "redirect_uri": self.REDIRECT_URI,
                    "enrollment": self.VALID_ENROLLMENT,
                },
            )

        assert resp.status_code == 200
        mock_identity_repo.resolve_or_create.assert_called_once_with("firebase", "fb-user-1")
        mock_service.enroll.assert_called_once()
        args, _ = mock_service.enroll.call_args
        assert args[0] == "pablo-user-1"
        assert args[1].install_id == "install-abc123"
        assert args[1].key_storage == "hardware"

    def test_exchange_without_enrollment_unchanged(self, client: TestClient) -> None:
        # Backward-compat path: clients pre-dating THERAPY-xo0o don't
        # send an enrollment block, and the exchange must still work.
        code = self._create_code(client)

        with (
            patch("app.routes.auth.get_identity_repository") as mock_get_repo,
            patch("app.routes.auth.get_companion_device_service") as mock_get_svc,
        ):
            resp = client.post(
                "/api/auth/native/exchange",
                json={"code": code, "redirect_uri": self.REDIRECT_URI},
            )

        assert resp.status_code == 200
        mock_get_repo.assert_not_called()
        mock_get_svc.assert_not_called()

    def test_enrollment_with_invalid_jwk_does_not_break_exchange(self, client: TestClient) -> None:
        # A malformed JWK from a buggy companion build must not block
        # the token return — the companion can retry enrollment later.
        code = self._create_code(client)

        bad_enrollment = {
            **self.VALID_ENROLLMENT,
            "device_public_key_jwk": {"kty": "EC", "crv": "P-256", "x": "abc"},  # no y
        }

        mock_identity_repo = MagicMock()
        mock_identity_repo.resolve_or_create.return_value = "pablo-user-1"

        with (
            patch(
                "app.routes.auth.get_identity_repository",
                return_value=mock_identity_repo,
            ),
        ):
            resp = client.post(
                "/api/auth/native/exchange",
                json={
                    "code": code,
                    "redirect_uri": self.REDIRECT_URI,
                    "enrollment": bad_enrollment,
                },
            )

        assert resp.status_code == 200
        assert resp.json()["id_token"] == "id_tok"

    def test_enrollment_rejects_unknown_platform(self, client: TestClient) -> None:
        code = self._create_code(client)
        bad = {**self.VALID_ENROLLMENT, "platform": "atari"}
        resp = client.post(
            "/api/auth/native/exchange",
            json={
                "code": code,
                "redirect_uri": self.REDIRECT_URI,
                "enrollment": bad,
            },
        )
        assert resp.status_code == 422

    def test_enrollment_rejects_unknown_key_storage(self, client: TestClient) -> None:
        code = self._create_code(client)
        bad = {**self.VALID_ENROLLMENT, "key_storage": "telepathic"}
        resp = client.post(
            "/api/auth/native/exchange",
            json={
                "code": code,
                "redirect_uri": self.REDIRECT_URI,
                "enrollment": bad,
            },
        )
        assert resp.status_code == 422
