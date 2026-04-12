# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for pre-auth endpoints (native code exchange).

SaaS-only tests (resolve-tenant, signup) are in test_saas_auth_routes.py.
"""

from collections.abc import Generator
from unittest.mock import patch

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
    def _mock_firebase(self) -> Generator[None, None, None]:
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
        assert "mismatch" in resp.json()["detail"]

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
